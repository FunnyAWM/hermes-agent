"""Super Agent router over durable Gateway-managed worker sessions."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
from pathlib import Path
from typing import Any

from agent.i18n import t
from hermes_cli.gateway_client import connect_gateway
from tools.environments.local_command_sandbox import resolve_binary, settings

from .store import SuperAgentStore


logger = logging.getLogger(__name__)
MAX_WORKERS = 2
POLL_SECONDS = 0.5
DEFAULT_WATCHDOG_SECONDS = 1800
GIT_TIMEOUT_SECONDS = 60
INLINE_CHAR_BUDGET = 8000
SPILL_NOTICE = "[Full content spilled to {ref}; read it from the workspace before continuing.]"


class SuperAgentRuntime:
    def __init__(self, ctx: Any, store: SuperAgentStore):
        self.ctx = ctx
        self.store = store
        self._runner: asyncio.Task | None = None
        self._watchdog: asyncio.Task | None = None
        self._workers: set[asyncio.Task] = set()
        self._wake = asyncio.Event()
        self._watchdog_wake = asyncio.Event()
        self._recovered = asyncio.Event()
        self._closing = False
        get_config = getattr(ctx, "get_config", lambda _name, default=None: default)
        self.watchdog_seconds = float(get_config("watchdog_seconds", DEFAULT_WATCHDOG_SECONDS))
        if not math.isfinite(self.watchdog_seconds) or self.watchdog_seconds <= 0:
            raise ValueError(t("super_agent.watchdog_config"))

    @property
    def max_workers(self) -> int:
        raw = self.ctx.get_config("max_concurrent_workers", str(MAX_WORKERS))
        try:
            value = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(t("super_agent.concurrency")) from exc
        if isinstance(raw, bool) or not 1 <= value <= 8 or str(value) != str(raw).strip():
            raise ValueError(t("super_agent.concurrency"))
        return value

    def worker_model(self) -> tuple[str, str]:
        values = []
        for key in ("worker_provider", "worker_model"):
            raw = self.ctx.get_config(key, "")
            if not isinstance(raw, str):
                raise ValueError(f"{key} must be a string")
            values.append(raw.strip())
        return values[0], values[1]

    def start(self) -> None:
        asyncio.get_running_loop()
        self._closing = False
        if self._runner is None or self._runner.done():
            self._runner = self.ctx.spawn_task(
                self._run(), name=f"plugin:{self.ctx.plugin_id}:router"
            )
        if self._watchdog is None or self._watchdog.done():
            self._watchdog = self.ctx.spawn_task(
                self._run_watchdog(), name=f"plugin:{self.ctx.plugin_id}:watchdog"
            )

    def wake(self) -> None:
        self.start()
        self._wake.set()
        self._watchdog_wake.set()

    def stop(self) -> None:
        self._closing = True
        self._wake.set()
        self._watchdog_wake.set()
        for task in tuple(self._workers):
            if not task.cancelling():
                task.cancel()
        if self._runner is not None and not self._runner.cancelling():
            self._runner.cancel()
        if self._watchdog is not None and not self._watchdog.cancelling():
            self._watchdog.cancel()

    async def submit(self, **kwargs: Any) -> tuple[dict, bool]:
        ticket, created = await asyncio.to_thread(self.store.submit, **kwargs)
        self.wake()
        return ticket, created

    async def tell(self, ticket_id: str, text: str) -> bool:
        accepted = await asyncio.to_thread(self.store.add_inbox, ticket_id, text)
        if accepted:
            self.wake()
        return accepted

    async def retry(self, ticket_id: str) -> bool:
        accepted = await asyncio.to_thread(self.store.retry, ticket_id)
        if accepted:
            self.wake()
        return accepted

    async def pause(self) -> bool:
        changed = await asyncio.to_thread(self.store.set_paused, True)
        self.wake()
        return changed

    async def resume(self) -> bool:
        changed = await asyncio.to_thread(self.store.set_paused, False)
        self.wake()
        return changed

    async def cancel(self, ticket_id: str) -> dict | None:
        return await self._stop_ticket(ticket_id, self.store.cancel)

    async def takeover(self, ticket_id: str) -> dict | None:
        return await self._stop_ticket(ticket_id, self.store.takeover)

    # --- SA-05: subscriptions and handback ---

    async def subscribe(self, ticket_id: str, source_instance: str, thread_key: str, mode: str = "follow") -> dict:
        thread = await asyncio.to_thread(self.store.ensure_thread, source_instance, thread_key)
        accepted = await asyncio.to_thread(self.store.subscribe, ticket_id, thread["id"], mode)
        if accepted:
            self.wake()
        return {"thread": thread, "accepted": accepted}

    async def unsubscribe(self, ticket_id: str, source_instance: str, thread_key: str) -> bool:
        thread = await asyncio.to_thread(self.store.find_thread, source_instance, thread_key)
        if thread is None:
            return False
        removed = await asyncio.to_thread(self.store.unsubscribe, ticket_id, thread["id"])
        if removed:
            self.wake()
        return removed

    async def handback(self, ticket_id: str, trigger_id: str, reason: str) -> bool:
        recorded = await asyncio.to_thread(self.store.record_handback, trigger_id, ticket_id, reason)
        if recorded:
            self.wake()
        return recorded

    # --- SA-06: observe mode ---

    async def fanout(self, source_instance: str, thread_key: str, text: str) -> list[str] | None:
        thread = await asyncio.to_thread(self.store.find_thread, source_instance, thread_key)
        if thread is None:
            return []
        if thread["accept_mode"] == "observe":
            await asyncio.to_thread(
                self.store.record_observe,
                thread_id=thread["id"], trigger_id=None,
                action="would_fanout", detail=text[:500],
            )
            return None
        if thread["accept_mode"] == "off":
            return None
        accepted = await asyncio.to_thread(self.store.fanout_to_subscribers, thread["id"], text)
        if accepted:
            self.wake()
        return accepted

    async def _stop_ticket(self, ticket_id: str, operation: Any) -> dict | None:
        assignment = await asyncio.to_thread(operation, ticket_id)
        if assignment is not None:
            self.wake()
            session_id = assignment.get("worker_session_id")
            admission_id = assignment.get("turn_admission_id") or assignment.get("admission_id")
            if session_id and admission_id:
                try:
                    async with connect_gateway(
                        purpose="managed-session", subject=f"plugin:{self.ctx.plugin_id}"
                    ) as client:
                        await client.rpc("session.resume", session_id=session_id)
                        await client.rpc(
                            "prompt.cancel", session_id=session_id, admission_id=admission_id
                        )
                except Exception:  # persisted cancellation remains authoritative
                    logger.exception("Super Agent remote stop failed for %s", ticket_id)
        return assignment

    async def _run(self) -> None:
        await asyncio.to_thread(self.store.recover)
        self._recovered.set()
        try:
            while not self._closing:
                self._workers = {task for task in self._workers if not task.done()}
                launched = False
                paused = await asyncio.to_thread(self.store.is_paused)
                if not paused:
                    while len(self._workers) < self.max_workers:
                        assignment = await asyncio.to_thread(self.store.claim_next)
                        if assignment is None:
                            break
                        task = asyncio.create_task(
                            self._run_assignment(assignment),
                            name=f"super-agent:{assignment['ticket_id']}",
                        )
                        self._workers.add(task)
                        task.add_done_callback(self._worker_done)
                        launched = True
                delivered = False if paused else await self._deliver_one()
                if not launched and not delivered:
                    self._wake.clear()
                    try:
                        await asyncio.wait_for(self._wake.wait(), timeout=POLL_SECONDS)
                    except TimeoutError:
                        pass
        finally:
            for task in tuple(self._workers):
                if not task.cancelling():
                    task.cancel()

    async def _run_watchdog(self) -> None:
        await self._recovered.wait()
        interval = min(30.0, max(1.0, self.watchdog_seconds / 4))
        while not self._closing:
            self._watchdog_wake.clear()
            try:
                alerts = await asyncio.to_thread(
                    self.store.scan_watchdog, self.watchdog_seconds
                )
                for alert in alerts:
                    logger.warning(
                        "Super Agent Watchdog: %s (%s)",
                        alert["ticket_id"], alert["detail"],
                    )
            except Exception:
                logger.exception("Super Agent Watchdog scan failed")
            try:
                await asyncio.wait_for(self._watchdog_wake.wait(), timeout=interval)
            except TimeoutError:
                pass

    def _worker_done(self, task: asyncio.Task) -> None:
        self._workers.discard(task)
        self._wake.set()
        if not task.cancelled() and task.exception() is not None:
            logger.error("Super Agent worker task failed: %s", task.exception())

    async def _run_assignment(self, assignment: dict) -> None:
        try:
            self._sandbox_preflight()
            workspace = await self._ensure_worktree(assignment)
            async with connect_gateway(
                purpose="managed-session", subject=f"plugin:{self.ctx.plugin_id}"
            ) as client:
                session_id = assignment.get("worker_session_id")
                if session_id:
                    await client.rpc("session.resume", session_id=session_id)
                else:
                    provider, model = self.worker_model()
                    snapshot = await client.rpc(
                        "session.create",
                        request_id=f"super-agent:{assignment['id']}",
                        source="managed_worker",
                        cwd=str(workspace),
                        cwd_explicit=True,
                        toolsets=["terminal"],
                        title=f"Super Agent {assignment['ticket_id']}",
                        **({"provider": provider} if provider else {}),
                        **({"model": model} if model else {}),
                    )
                    session_id = snapshot["session_id"]
                    await asyncio.to_thread(
                        self.store.bind_worker_session, assignment["id"], session_id
                    )
                await self._run_turns(client, assignment, session_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Super Agent assignment failed for %s", assignment["id"])
            await asyncio.to_thread(self.store.fail_assignment, assignment["id"], str(exc))
            self._wake.set()
            self._watchdog_wake.set()

    def _sandbox_preflight(self) -> None:
        cfg = settings()
        if not cfg.enabled:
            raise RuntimeError(t("super_agent.enable_sandbox"))
        if resolve_binary(cfg) is None:
            raise RuntimeError(t("super_agent.sandctl"))

    async def _ensure_worktree(self, assignment: dict) -> Path:
        root = await asyncio.to_thread(
            lambda: Path(assignment["workspace_root"]).expanduser().resolve()
        )
        workspace = await asyncio.to_thread(lambda: Path(assignment["workspace"]).resolve())
        top = await self._git("-C", str(root), "rev-parse", "--show-toplevel")
        resolved_top = await asyncio.to_thread(Path(top).resolve)
        if os.path.normcase(str(resolved_top)) != os.path.normcase(str(root)):
            raise RuntimeError(t("super_agent.git_root", root=root))
        if await asyncio.to_thread(workspace.exists):
            existing = await self._git("-C", str(workspace), "rev-parse", "--show-toplevel")
            resolved_existing = await asyncio.to_thread(Path(existing).resolve)
            if os.path.normcase(str(resolved_existing)) != os.path.normcase(str(workspace)):
                raise RuntimeError(t("super_agent.wrong_tree", workspace=workspace))
            return workspace
        if await self._git("-C", str(root), "status", "--porcelain"):
            raise RuntimeError(t("super_agent.dirty", root=root))
        await asyncio.to_thread(workspace.parent.mkdir, parents=True, exist_ok=True)
        await self._git(
            "-C", str(root), "worktree", "add", "--detach", str(workspace), "HEAD"
        )
        return workspace

    @staticmethod
    async def _git(*args: str) -> str:
        process = await asyncio.create_subprocess_exec(
            "git",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=GIT_TIMEOUT_SECONDS
            )
        except TimeoutError:
            process.kill()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except TimeoutError:
                raise RuntimeError(f"git {' '.join(args)} timed out and did not terminate") from None
            raise RuntimeError(f"git {' '.join(args)} timed out") from None
        if process.returncode:
            detail = stderr.decode(errors="replace").strip() or stdout.decode(errors="replace").strip()
            raise RuntimeError(f"git {' '.join(args)} failed: {detail}")
        return stdout.decode(errors="replace").strip()

    async def _run_turns(self, client: Any, assignment: dict, session_id: str) -> None:
        initial = self._inline_or_spill(
            assignment["ticket_id"],
            "Complete the task in the current Git worktree. Work autonomously and return a concise "
            "summary, validation evidence, and changed files.\n\n"
            f"Task:\n{assignment['task']}",
        )
        await asyncio.to_thread(self.store.ensure_initial_turn, assignment["id"], initial)
        last_result = ""
        while True:
            turn = await asyncio.to_thread(self.store.pending_turn, assignment["id"])
            if turn is None:
                turn = await asyncio.to_thread(self.store.create_followup_turn, assignment["id"])
            if turn is None:
                await asyncio.to_thread(
                    self.store.complete_if_idle, assignment["id"], last_result
                )
                return
            result = await self._execute_turn(client, session_id, turn)
            last_result = result
            await asyncio.to_thread(self.store.mark_turn_terminal, turn["id"], result)
            self._watchdog_wake.set()

    def _inline_or_spill(self, ticket_id: str, text: str) -> str:
        """Keep inline content under budget; spill oversized content to a file reference."""
        if len(text) <= INLINE_CHAR_BUDGET:
            return text
        ref = self.store.spill_content(ticket_id, text)
        return text[:INLINE_CHAR_BUDGET] + "\n\n" + SPILL_NOTICE.format(ref=ref)

    async def _execute_turn(self, client: Any, session_id: str, turn: dict) -> str:
        admission_id = turn.get("admission_id")
        import time as _time
        turn_start = _time.monotonic()
        try:
            if not admission_id:
                accepted = await client.rpc(
                    "prompt.submit",
                    session_id=session_id,
                    input_id=turn["input_id"],
                    text=turn["text"],
                    finite=True,
                    unattended=True,
                )
                admission_id = accepted["admission_id"]
                await asyncio.to_thread(
                    self.store.mark_turn_submitted, turn["id"], admission_id
                )
            while True:
                receipt = await client.rpc(
                    "prompt.receipt",
                    session_id=session_id,
                    admission_id=admission_id,
                    include_result=True,
                )
                status = receipt.get("status")
                if status == "unknown":
                    raise RuntimeError(
                        f"Worker turn outcome is unknown for admission {admission_id}; "
                        "inspect the Session before retrying"
                    )
                if status == "terminal":
                    outcome = receipt.get("outcome")
                    result = receipt.get("result")
                    if outcome != "completed" or not isinstance(result, dict):
                        raise RuntimeError(f"Worker turn ended with outcome {outcome or 'unknown'}")
                    if result.get("failed") or result.get("interrupted"):
                        raise RuntimeError(
                            str(result.get("error") or f"Worker turn ended with outcome {outcome}")
                        )
                    usage = result.get("usage") or {}
                    metrics = {
                        "input_tokens": usage.get("input_tokens") or usage.get("prompt_tokens"),
                        "output_tokens": usage.get("output_tokens") or usage.get("completion_tokens"),
                        "cache_read_tokens": usage.get("cache_read_tokens") or usage.get("cache_read_input_tokens"),
                        "duration_seconds": round(_time.monotonic() - turn_start, 2),
                    }
                    await asyncio.to_thread(self.store.record_turn_metrics, turn["id"], metrics)
                    return str(result.get("final_response") or "Completed without a final response.")
                await asyncio.sleep(POLL_SECONDS)
        except asyncio.CancelledError:
            if admission_id:
                try:
                    await client.rpc(
                        "prompt.cancel", session_id=session_id, admission_id=admission_id
                    )
                except Exception:
                    logger.exception(
                        "Super Agent unload cancel failed for admission %s", admission_id
                    )
            raise

    async def _deliver_one(self) -> bool:
        row = await asyncio.to_thread(self.store.claim_outbox)
        if row is None:
            return False
        try:
            source = json.loads(row["source_json"])
            handle = await self.ctx.apost_message(row["text"], source=source)
            await asyncio.to_thread(
                self.store.settle_outbox,
                row["id"],
                delivered=handle.ok,
                error=handle.error or "",
            )
        except Exception as exc:
            logger.exception("Super Agent outbox delivery failed for %s", row["id"])
            await asyncio.to_thread(
                self.store.settle_outbox, row["id"], delivered=False, error=str(exc)
            )
        return True
