"""Super Agent router over durable Gateway-managed worker sessions."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
from pathlib import Path
from tempfile import TemporaryFile
import time
from contextlib import suppress
from typing import Any

from agent.i18n import t
from hermes_cli.gateway_client import connect_gateway
from tools.environments.local_command_sandbox import resolve_binary, settings

from .delivery import SEND_TIMEOUT_SECONDS
from .store import SuperAgentStore
from .sources import MessageSources
from .workspace import WORKTREE_DIRECTORY, has_checkout_changes
from .worker_input import initial_prompt, worker_prompt
from .worker_turn import execute_turn


logger = logging.getLogger(__name__)
MAX_WORKERS = 2
POLL_SECONDS = 0.5
DEFAULT_WATCHDOG_SECONDS = 1800
GIT_TIMEOUT_SECONDS = 60


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
        self.scheduler_error = ""
        self._stopping: set[str] = set()
        self._last_prune = -math.inf
        get_config = getattr(ctx, "get_config", lambda _name, default=None: default)
        self.watchdog_seconds = float(get_config("watchdog_seconds", DEFAULT_WATCHDOG_SECONDS))
        if not math.isfinite(self.watchdog_seconds) or self.watchdog_seconds <= 0:
            raise ValueError(t("super_agent.watchdog_config"))
        self.max_workers = self._worker_limit(get_config("max_concurrent_workers", str(MAX_WORKERS)))
        self._turn_limit(get_config("turn_timeout_seconds", 0))

    @staticmethod
    def _turn_limit(raw) -> float:
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(t("super_agent.turn_timeout_config")) from exc
        if isinstance(raw, bool) or not math.isfinite(value) or value < 0:
            raise ValueError(t("super_agent.turn_timeout_config"))
        return value

    @staticmethod
    def _worker_limit(raw) -> int:
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
        try:
            self.start()
        except Exception as exc:
            # The request has already committed; a scheduler failure must not hand it to another agent.
            self.scheduler_error = str(exc)
            logger.exception("Could not wake Super Agent scheduler")
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

    async def tell(self, ticket_id: str, text: str, *, message_id: str = "") -> bool:
        accepted = await asyncio.to_thread(self.store.add_inbox, ticket_id, text, message_id=message_id)
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

    async def cancel(self, ticket_id: str, *, withdrawal=None) -> dict | None:
        return await self._stop_ticket(ticket_id, self.store.cancel, withdrawal=withdrawal)

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

    async def fanout(self, thread: dict, text: str, *, message_id: str) -> list[str] | None:
        if thread["accept_mode"] == "observe":
            await asyncio.to_thread(
                self.store.record_observe,
                thread_id=thread["id"], trigger_id=None,
                action="would_fanout", detail=text[:500],
            )
            return None
        if thread["accept_mode"] == "off":
            return None
        accepted = await asyncio.to_thread(self.store.fanout_to_subscribers, thread["id"], text, message_id)
        if accepted:
            self.wake()
        return accepted

    async def _stop_ticket(self, ticket_id: str, operation: Any, **kwargs) -> dict | None:
        assignment = await asyncio.to_thread(operation, ticket_id, **kwargs)
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
        delivery = asyncio.create_task(self._deliver_loop(), name="super-agent:delivery")
        try:
            while not self._closing:
                self._wake.clear()
                try:
                    if not self._recovered.is_set():
                        await asyncio.to_thread(self.store.recover)
                        self._recovered.set()
                    await self._dispatch()
                    self.scheduler_error = ""
                except Exception as exc:
                    if self.scheduler_error != str(exc):
                        logger.exception("Super Agent scheduling failed")
                    self.scheduler_error = str(exc)
                with suppress(TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), timeout=POLL_SECONDS if not self.scheduler_error else 2)
        finally:
            delivery.cancel()
            with suppress(asyncio.CancelledError):
                await delivery
            for task in tuple(self._workers):
                if not task.cancelling():
                    task.cancel()
            if self._workers:
                await asyncio.gather(*self._workers, return_exceptions=True)

    async def _dispatch(self) -> None:
        self._workers = {task for task in self._workers if not task.done()}
        occupied = {task.get_name() for task in self._workers}
        for stop in await asyncio.to_thread(self.store.pending_stops):
            name = f"super-agent:{stop['assignment_id']}"
            if not stop["admission_id"] and name in occupied:
                continue  # Let an in-flight submit capture its admission before reconciling the snapshot.
            key = stop["id"]
            if key not in self._stopping:
                self._stopping.add(key)
                task = asyncio.create_task(self._reconcile_stop(stop), name=name)
                self._workers.add(task)
                occupied.add(name)
                task.add_done_callback(self._worker_done)
        if await asyncio.to_thread(self.store.is_paused):
            return
        while len(occupied) < self.max_workers:
            assignment = await asyncio.to_thread(self.store.claim_next)
            if assignment is None:
                break
            name = f"super-agent:{assignment['id']}"
            task = asyncio.create_task(self._run_assignment(assignment), name=name)
            self._workers.add(task)
            occupied.add(name)
            task.add_done_callback(self._worker_done)

    async def _reconcile_stop(self, stop: dict) -> None:
        try:
            while not self._closing:
                try:
                    async with connect_gateway(purpose="managed-session", subject=f"plugin:{self.ctx.plugin_id}") as client:
                        snapshot = await client.rpc("session.resume", session_id=stop["worker_session_id"])
                        admission = stop["admission_id"] or next((row["admission_id"] for row in snapshot["pending"]
                                                                 if row["input_id"] == stop["input_id"]), None)
                        if not admission:
                            await asyncio.to_thread(self.store.confirm_stopped, stop["id"])
                            return
                        args = dict(session_id=stop["worker_session_id"], admission_id=admission)
                        receipt = await client.rpc("prompt.receipt", **args)
                        if receipt.get("status") not in {"terminal", "unknown"}:
                            receipt = await client.rpc("prompt.cancel", **args)
                        while receipt.get("status") != "terminal":
                            # Unknown needs an owner's resolution. Polling has no side effects.
                            await asyncio.sleep(5 if receipt.get("status") == "unknown" else POLL_SECONDS)
                            receipt = await client.rpc("prompt.receipt", **args)
                    await asyncio.to_thread(self.store.confirm_stopped, stop["id"])
                    return
                except Exception:
                    logger.warning("Awaiting Worker stop confirmation for %s", stop["ticket_id"], exc_info=True)
                    await asyncio.sleep(2)
        finally:
            self._stopping.discard(stop["id"])

    async def _deliver_loop(self) -> None:
        await self._recovered.wait()
        while not self._closing:
            try:
                if await self._deliver_one():
                    continue
            except Exception:
                logger.exception("Super Agent delivery queue unavailable")
            await asyncio.sleep(POLL_SECONDS)

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
                if time.monotonic() - self._last_prune >= 3600:
                    days = self.ctx.get_config("history_retention_days", 0)
                    if days:
                        await asyncio.to_thread(self.store.prune_history, days)
                    self._last_prune = time.monotonic()
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
                        title=f"Super Agent {assignment['ticket_id']} g{assignment['generation']}",
                        **({"provider": provider} if provider else {}),
                        **({"model": model} if model else {}),
                    )
                    session_id = snapshot["session_id"]
                    await asyncio.to_thread(
                        self.store.bind_worker_session, assignment["id"], session_id
                    )
                progress = asyncio.create_task(self._consume_progress(client.events, session_id))
                try:
                    await self._run_turns(client, assignment, session_id, workspace=workspace)
                finally:
                    progress.cancel()
                    with suppress(asyncio.CancelledError):
                        await progress
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Super Agent assignment failed for %s", assignment["id"])
            await asyncio.to_thread(self.store.fail_assignment, assignment["id"], str(exc),
                                    home_platforms=self._notification_homes())
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
        assigned_path = Path(assignment["workspace"])
        workspace = await asyncio.to_thread(assigned_path.resolve)
        if assigned_path.parent == Path(assignment["workspace_root"]) / WORKTREE_DIRECTORY and workspace != assigned_path:
            raise RuntimeError(t("super_agent.wrong_tree", workspace=assigned_path))
        top = await self._git("-C", str(root), "rev-parse", "--show-toplevel")
        resolved_top = await asyncio.to_thread(Path(top).resolve)
        if os.path.normcase(str(resolved_top)) != os.path.normcase(str(root)):
            raise RuntimeError(t("super_agent.git_root", root=root))
        if await asyncio.to_thread(workspace.exists):
            existing = await self._git("-C", str(workspace), "rev-parse", "--show-toplevel")
            resolved_existing = await asyncio.to_thread(Path(existing).resolve)
            if os.path.normcase(str(resolved_existing)) != os.path.normcase(str(workspace)):
                raise RuntimeError(t("super_agent.wrong_tree", workspace=workspace))
            common = ("rev-parse", "--path-format=absolute", "--git-common-dir")
            if Path(await self._git("-C", str(workspace), *common)) != Path(await self._git("-C", str(root), *common)):
                raise RuntimeError(t("super_agent.wrong_tree", workspace=workspace))
            return workspace
        status = await self._git("-C", str(root), "status", "--porcelain=v2", "-z", "-uall", "--ignore-submodules=dirty")
        if has_checkout_changes(status):
            raise RuntimeError(t("super_agent.dirty", root=root))
        await asyncio.to_thread(workspace.parent.mkdir, parents=True, exist_ok=True)
        await self._git(
            "-C", str(root), "worktree", "add", "--detach", str(workspace), "HEAD"
        )
        return workspace

    @staticmethod
    async def _git(*args: str) -> str:
        # Files also work on restricted Windows hosts where asyncio cannot create named pipes.
        with TemporaryFile() as stdout, TemporaryFile() as stderr:
            process = await asyncio.create_subprocess_exec("git", *args, stdout=stdout, stderr=stderr)
            reaped = asyncio.create_task(process.wait())  # health: allow HX006 -- Both callers below time out; the shielded task still reaps a late exit.
            try:
                await asyncio.wait_for(asyncio.shield(reaped), timeout=GIT_TIMEOUT_SECONDS)
            except (TimeoutError, asyncio.CancelledError) as exc:
                if process.returncode is None:
                    with suppress(ProcessLookupError):
                        process.kill()
                try:
                    await asyncio.wait_for(asyncio.shield(reaped), timeout=5)
                except TimeoutError:
                    logger.warning("Git process is still exiting after termination")
                if isinstance(exc, asyncio.CancelledError):
                    raise
                raise RuntimeError(f"git {' '.join(args)} timed out") from None
            stdout.seek(0)
            output = stdout.read().decode(errors="replace").strip()
            if process.returncode:
                stderr.seek(0)
                detail = stderr.read().decode(errors="replace").strip() or output
                raise RuntimeError(f"git {' '.join(args)} failed: {detail}")
            return output

    def _notification_homes(self):
        try:
            return MessageSources.from_config(self.ctx).write_only(self.ctx)
        except Exception:
            logger.exception("Cannot select Super Agent notification destinations")
            return None

    async def _consume_progress(self, events, session_id: str) -> None:
        last_write = -math.inf
        while True:
            item = await events.get()
            if not isinstance(item, dict):
                if isinstance(item, Exception):
                    # The Gateway queues its disconnect error as the stream's last item.
                    logger.debug("Super Agent progress stream ended: %s", item)
                    return
                continue
            frame = item.get("params") or {}
            if frame.get("session_id") != session_id or frame.get("type") not in {
                "tool.start", "tool.complete", "message.delta",
            }:
                continue
            admission = frame.get("admission_id")
            now = time.monotonic()
            if admission and now - last_write >= min(5.0, self.watchdog_seconds / 4):
                try:
                    await asyncio.to_thread(self.store.note_worker_progress, session_id, admission)
                except Exception:
                    logger.warning("Unable to record Worker progress for %s", admission, exc_info=True)
                last_write = now

    async def _run_turns(self, client: Any, assignment: dict, session_id: str, *, workspace: Path | None = None) -> None:
        workspace = workspace or Path(assignment["workspace"])
        initial = initial_prompt(assignment["task"])
        await asyncio.to_thread(self.store.ensure_initial_turn, assignment["id"], initial)
        last_result = ""
        while True:
            turn = await asyncio.to_thread(self.store.pending_turn, assignment["id"])
            if turn is None:
                if await asyncio.to_thread(self.store.complete_if_idle, assignment["id"], last_result,
                                           home_platforms=self._notification_homes()):
                    return
                turn = await asyncio.to_thread(self.store.create_followup_turn, assignment["id"])
                if turn is None:
                    return
            timeout = self._turn_limit(await asyncio.to_thread(self.ctx.get_config, "turn_timeout_seconds", 0))
            turn = await asyncio.to_thread(self.store.start_turn, turn["id"], timeout)
            if turn is None:
                return
            # Recreate a legacy spill even when the Gateway is already running its original prompt.
            text = await asyncio.to_thread(worker_prompt, workspace, turn, initial, assignment["ticket_id"],
                                          self.store.record_input)
            result, metrics = await execute_turn(self.store, client, session_id, turn, text, POLL_SECONDS)
            last_result = result
            await asyncio.to_thread(self.store.mark_turn_terminal, turn["id"], result,
                                    home_platforms=self._notification_homes(), metrics=metrics)
            self._watchdog_wake.set()


    async def _post_report(self, row, source):
        permissions = MessageSources.from_config(self.ctx)
        home = source.get("home_channel")
        platform = home or source["platform"]
        if not permissions.writes(platform) or permissions.reads(platform) != (home is None):
            return False, "source_disabled", None, None
        key = f"{row['ticket_id']}:{row['kind']}"
        external = {"delivery_id": key}
        if row["history_checkpoint"]:
            external["checkpoint"] = json.loads(row["history_checkpoint"])
        if platform == "local" and source.get("local_session_id"):
            handle = await self.ctx.apost_local_session(row["text"], session_id=source["local_session_id"],
                                                        actor_id=source["actor_id"], delivery_id=key)
        elif platform == "local" and (home or source.get("bot_chat")):
            handle = await self.ctx.apost_home_channel(row["text"], platform=platform, delivery_id=key)
        elif home:
            handle = await self.ctx.apost_home_channel(row["text"], platform=platform, **external)
        else:
            handle = await self.ctx.apost_message(row["text"], source=source, editable=False, **external)
        return (handle.ok, handle.error or "", getattr(handle, "message_id", None),
                getattr(handle, "result", {}).get("delivery_checkpoint"))

    async def _deliver_one(self) -> bool:
        row = await asyncio.to_thread(self.store.claim_outbox)
        if row is None:
            return False
        checkpoint = None
        try:
            source = json.loads(row["source_json"])
            if source.get("discover_homes"):
                platforms = self._notification_homes()
                if platforms is not None:
                    await asyncio.to_thread(self.store.expand_outbox_homes, row, platforms)
                    return True
                delivered, error, receipt = False, "home_discovery_unavailable", None
            else:
                async with asyncio.timeout(SEND_TIMEOUT_SECONDS):
                    delivered, error, receipt, checkpoint = await self._post_report(row, source)
        except TimeoutError:
            delivered, error, receipt = False, "timeout", None
        except Exception as exc:
            logger.exception("Super Agent outbox delivery failed for %s", row["id"])
            delivered, error, receipt = False, str(exc) or type(exc).__name__, None
        await asyncio.to_thread(self.store.settle_outbox, row["id"], delivered=delivered,
                                error=error, receipt=receipt, attempt=row["attempts"], checkpoint=checkpoint)
        return True
