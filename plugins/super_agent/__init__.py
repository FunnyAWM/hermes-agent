"""Opt-in Super Agent Router plugin."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import hashlib
import logging
from pathlib import Path
import uuid
from typing import Any

from agent.runtime_session_store import is_worker_process

from .runtime import DEFAULT_WATCHDOG_SECONDS, SuperAgentRuntime
from .store import MAX_RETRIES, SANDBOX_LEVELS, SuperAgentStore


logger = logging.getLogger(__name__)


_HELP = """Super Agent commands:
/super-agent new [read-only|workspace-write|workspace-exec] <task>
/super-agent status <ticket-id>
/super-agent list
/super-agent tell <ticket-id> <message>
/super-agent stop|cancel <ticket-id>
/super-agent retry <ticket-id>
/super-agent takeover <ticket-id>
/super-agent pause|resume
/super-agent health
/super-agent subscribe <ticket-id> <thread-key> [follow|context]
/super-agent unsubscribe <ticket-id> <thread-key>
/super-agent handback <ticket-id> <trigger-id> <reason>
/super-agent observe <thread-key> [off|observe|auto]"""


def _sandbox_hook(store: SuperAgentStore, tool_name: str, args: dict, session_id: str = "", **_kwargs: Any):
    if tool_name != "terminal":
        return None
    sandbox = store.assignment_sandbox(session_id)
    if sandbox is None:
        return {"action": "block", "message": "Super Agent Worker has no active sandbox assignment."}
    return {
        "action": "modify",
        "args": {**args, "sandbox": sandbox, "_host_local": True, "_sandbox_required": True},
    }


def _workspace_root(ctx: Any) -> str:
    raw = str(ctx.get_config("workspace_root", "") or "").strip()
    if not raw:
        raise ValueError(
            f"Set plugins.entries.{ctx.plugin_id}.settings.workspace_root to an absolute Git repository root."
        )
    root = Path(raw).expanduser()
    if not root.is_absolute() or not root.is_dir():
        raise ValueError("Super Agent workspace_root must be an existing absolute directory.")
    return str(root.resolve())


def _source_snapshot(source: Any) -> dict | None:
    if source is None:
        return None
    if hasattr(source, "to_dict"):
        source = source.to_dict()
    if not isinstance(source, Mapping) or source.get("platform") == "local":
        return None
    return dict(source)


def _source_instance(source: dict | None, fallback: str) -> str:
    if not source:
        return fallback
    return ":".join(str(source.get(key) or "") for key in ("platform", "chat_id", "thread_id"))


def _allows_message_source(ctx: Any, source: Mapping[str, Any]) -> bool:
    raw = ctx.get_config("message_sources", ["*"])
    if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
        raise ValueError("message_sources must be a JSON list of Gateway platform IDs")
    allowed = {item.strip().lower() for item in raw if item.strip()}
    platform = str(source.get("platform") or "").strip().lower()
    return "*" in allowed or platform in allowed


def _parse_new(ctx: Any, text: str) -> tuple[str, str]:
    text = text.strip()
    if not text:
        raise ValueError("A task is required.")
    first, separator, rest = text.partition(" ")
    level = first.lower()
    if level == "full-access":
        raise ValueError("full-access is not available to Super Agent Workers.")
    if level in SANDBOX_LEVELS:
        if not separator or not rest.strip():
            raise ValueError("A task is required after the sandbox level.")
        return level, rest.strip()
    sandbox = str(ctx.get_config("default_sandbox", "workspace-write") or "workspace-write").strip()
    if sandbox not in SANDBOX_LEVELS:
        raise ValueError("default_sandbox must be read-only, workspace-write, or workspace-exec.")
    return sandbox, text


async def _new_ticket(
    ctx: Any,
    runtime: SuperAgentRuntime,
    text: str,
    *,
    source: Any = None,
    source_instance: str = "local",
    external_event_id: str | None = None,
) -> str:
    sandbox, task = _parse_new(ctx, text)
    snapshot = _source_snapshot(source)
    ticket, created = await runtime.submit(
        task=task,
        sandbox=sandbox,
        workspace_root=_workspace_root(ctx),
        source_instance=_source_instance(snapshot, source_instance),
        external_event_id=external_event_id or uuid.uuid4().hex,
        source=snapshot,
    )
    duplicate = " (already queued)" if not created else ""
    paused = await asyncio.to_thread(runtime.store.is_paused)
    waiting = " Router is paused; launch and delivery will resume explicitly." if paused else ""
    return f"Super Agent {ticket['id']} queued with {ticket['sandbox']} sandbox{duplicate}.{waiting}"


def _ticket_text(ticket: dict) -> str:
    assignment = ticket.get("assignment") or {}
    lines = [
        f"{ticket['id']}: {ticket['status']} ({ticket['sandbox']})",
        f"Task: {ticket['task']}",
    ]
    if assignment.get("worker_session_id"):
        lines.append(f"Worker Session: {assignment['worker_session_id']} (read-only)")
    if assignment.get("workspace"):
        lines.append(f"Worktree: {assignment['workspace']}")
    alert = ticket.get("watchdog_alert")
    if alert:
        lines.extend((f"Watchdog: {alert['detail']}", f"Suggested action: {alert['suggestion']}"))
    lines.append(f"Retry budget: {ticket['retry_count']}/{MAX_RETRIES}")
    if ticket.get("last_error"):
        lines.append(f"Error: {ticket['last_error']}")
    if ticket.get("result"):
        lines.append(f"Result: {ticket['result']}")
    return "\n".join(lines)


async def _router_command(runtime: SuperAgentRuntime, command: str) -> str | None:
    if command == "pause":
        changed = await runtime.pause()
        return "Super Agent Router paused." if changed else "Super Agent Router is already paused."
    if command == "resume":
        changed = await runtime.resume()
        return "Super Agent Router resumed." if changed else "Super Agent Router is already running."
    if command in {"health", "watchdog"}:
        state = await asyncio.to_thread(runtime.store.router_state)
        counts = await asyncio.to_thread(runtime.store.counts)
        alerts = await asyncio.to_thread(runtime.store.list_watchdog_alerts)
        events = await asyncio.to_thread(runtime.store.list_control_events)
        threshold = getattr(runtime, "watchdog_seconds", DEFAULT_WATCHDOG_SECONDS)
        lines = [
            f"Router: {'paused' if state['paused'] else 'running'}",
            f"Watchdog: {len(alerts)} active; threshold {threshold:g}s",
            "Tickets: " + (
                ", ".join(f"{key}={value}" for key, value in sorted(counts.items())) or "none"
            ),
        ]
        lines.extend(
            f"Alert {alert['ticket_id']}: {alert['detail']} {alert['suggestion']}"
            for alert in alerts
        )
        if events:
            lines.append("Recent controls: " + "; ".join(
                f"{event['action']} {event['ticket_id'] or 'router'}" for event in events
            ))
        return "\n".join(lines)
    if command == "list":
        tickets = await asyncio.to_thread(runtime.store.list_tickets)
        state = await asyncio.to_thread(runtime.store.router_state)
        alerts = await asyncio.to_thread(runtime.store.list_watchdog_alerts)
        header = (
            f"Router: {'paused' if state['paused'] else 'running'}; "
            f"Watchdog alerts: {len(alerts)}"
        )
        return "\n".join([header, *(_ticket_text(ticket).splitlines()[0] for ticket in tickets)])
    if command == "observe":
        return None  # handled in _command with thread context
    return None


async def _ticket_command(
    runtime: SuperAgentRuntime, command: str, ticket_id: str, value: str, source_instance: str,
) -> str:
    if command == "status":
        ticket = await asyncio.to_thread(runtime.store.get_ticket, ticket_id)
        if ticket is None:
            return f"Unknown Super Agent ticket: {ticket_id}"
        events = await asyncio.to_thread(runtime.store.list_control_events, ticket_id)
        text = _ticket_text(ticket)
        if events:
            text += "\nRecovery record: " + "; ".join(event["action"] for event in events)
        return text
    if command == "tell":
        if not value.strip():
            return "Usage: /super-agent tell <ticket-id> <message>"
        return (
            f"Follow-up queued for {ticket_id}."
            if await runtime.tell(ticket_id, value)
            else f"Ticket {ticket_id} is missing or already terminal."
        )
    if command in {"stop", "cancel"}:
        return (
            f"Super Agent {ticket_id} cancelled."
            if await runtime.cancel(ticket_id) is not None
            else f"Ticket {ticket_id} is missing or already terminal."
        )
    if command == "takeover":
        return (
            f"Super Agent {ticket_id} taken over; its Worker Session and worktree were retained."
            if await runtime.takeover(ticket_id) is not None
            else f"Ticket {ticket_id} is missing or already terminal."
        )
    if command == "retry":
        return (
            f"Super Agent {ticket_id} queued for retry."
            if await runtime.retry(ticket_id)
            else f"Ticket {ticket_id} is missing, not retryable, or reached the {MAX_RETRIES}-retry limit."
        )
    if command == "subscribe":
        thread_key, _, mode = value.strip().partition(" ")
        if not thread_key:
            return "Usage: /super-agent subscribe <ticket-id> <thread-key> [follow|context]"
        mode = (mode.strip() or "follow").lower()
        if mode not in ("follow", "context"):
            return "Subscription mode must be follow or context."
        result = await runtime.subscribe(ticket_id, source_instance, thread_key, mode)
        if result["accepted"]:
            return f"Ticket {ticket_id} subscribed to {thread_key} ({mode})."
        return f"Ticket {ticket_id} is missing, terminal, or already subscribed."
    if command == "unsubscribe":
        thread_key = value.strip()
        if not thread_key:
            return "Usage: /super-agent unsubscribe <ticket-id> <thread-key>"
        removed = await runtime.unsubscribe(ticket_id, source_instance, thread_key)
        return (
            f"Ticket {ticket_id} unsubscribed from {thread_key}."
            if removed else f"No subscription found for {ticket_id} on {thread_key}."
        )
    if command == "handback":
        trigger_id, _, reason = value.strip().partition(" ")
        if not trigger_id or not reason.strip():
            return "Usage: /super-agent handback <ticket-id> <trigger-id> <reason>"
        recorded = await runtime.handback(ticket_id, trigger_id, reason.strip())
        return (
            f"Trigger {trigger_id} handed back from {ticket_id}."
            if recorded else f"Could not record handback for {ticket_id}."
        )
    return _HELP


async def _command(
    ctx: Any,
    runtime: SuperAgentRuntime,
    raw_args: str,
    *,
    source: Any = None,
    session_key: str = "",
    **_kwargs: Any,
) -> str:
    command, _, rest = raw_args.strip().partition(" ")
    command = command.lower()
    if command in {"", "help", "-h", "--help"}:
        return _HELP
    if command == "new":
        try:
            return await _new_ticket(
                ctx, runtime, rest, source=source, source_instance=f"slash:{session_key or 'local'}"
            )
        except (OSError, ValueError) as exc:
            return f"Super Agent request rejected: {exc}"
    source_instance = f"gateway:{session_key}" if _source_snapshot(source) else "slash"
    if command == "observe":
        thread_key, _, mode = rest.strip().partition(" ")
        if not thread_key:
            return "Usage: /super-agent observe <thread-key> [off|observe|auto]"
        mode = (mode.strip() or "observe").lower()
        if mode not in ("off", "observe", "auto"):
            return "Accept mode must be off, observe, or auto."
        thread = await asyncio.to_thread(runtime.store.ensure_thread, source_instance, thread_key, mode)
        changed = await asyncio.to_thread(runtime.store.set_thread_accept_mode, thread["id"], mode)
        action = f"set to {mode}" if changed else f"already {mode}"
        return f"Thread {thread_key} accept mode {action}."
    router_reply = await _router_command(runtime, command)
    if router_reply is not None:
        return router_reply
    ticket_id, _, value = rest.strip().partition(" ")
    if not ticket_id:
        return _HELP
    return await _ticket_command(runtime, command, ticket_id, value, source_instance)


async def _post_gateway_admission(
    ctx: Any,
    runtime: SuperAgentRuntime,
    *,
    text: str,
    source: dict,
    message_id: str = "",
    session_key: str = "",
    **_kwargs: Any,
):
    prefix, separator, task = text.partition(":")
    is_sa = separator and prefix.strip().lower() == "sa"
    try:
        if not _allows_message_source(ctx, source):
            return None
    except ValueError as exc:
        return {"action": "handled", "reply": f"Super Agent request rejected: {exc}"} if is_sa else None
    if not is_sa:
        thread_key = ":".join(
            str(source.get(key) or "") for key in ("platform", "chat_id", "thread_id")
        )
        fanout_tickets = await runtime.fanout(f"gateway:{session_key}", thread_key, text)
        if fanout_tickets:
            return {"action": "handled", "reply": f"Forwarded to {len(fanout_tickets)} Super Agent ticket(s)."}
        return None
    try:
        event_id = message_id or hashlib.sha256(
            f"{session_key}\0{text}".encode()
        ).hexdigest()
        reply = await _new_ticket(
            ctx,
            runtime,
            task,
            source=source,
            source_instance=f"gateway:{session_key}",
            external_event_id=event_id,
        )
    except Exception as exc:
        logger.exception("Super Agent gateway admission failed")
        reply = f"Super Agent request rejected: {exc}"
    return {"action": "handled", "reply": reply}


def register(ctx: Any) -> None:
    store = SuperAgentStore(ctx.state.data_dir, ctx.profile_name)
    if is_worker_process():
        ctx.register_hook(
            "pre_tool_call",
            lambda **kwargs: _sandbox_hook(store, **kwargs),
        )
        return

    runtime = SuperAgentRuntime(ctx, store)
    ctx.register_hook(
        "post_gateway_admission",
        lambda **kwargs: _post_gateway_admission(ctx, runtime, **kwargs),
    )
    ctx.register_command(
        "super-agent",
        lambda raw_args, **kwargs: _command(ctx, runtime, raw_args, **kwargs),
        description="Queue and control isolated Super Agent Worker sessions.",
        args_hint="<new|status|list|tell|stop|retry|takeover|pause|resume|health> ...",
        argument_mode="text",
    )
    ctx.on_unload(runtime.stop)
    try:
        runtime.start()
    except RuntimeError:
        pass
