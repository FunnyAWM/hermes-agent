"""Opt-in Super Agent Router plugin."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import logging
from pathlib import Path
import uuid
from typing import Any

from agent.i18n import t
from agent.runtime_session_store import is_worker_process

from .runtime import DEFAULT_WATCHDOG_SECONDS, SuperAgentRuntime
from .store import MAX_RETRIES, SANDBOX_LEVELS, SuperAgentStore


logger = logging.getLogger(__name__)


def _sandbox_hook(store: SuperAgentStore, tool_name: str, args: dict, session_id: str = "", **_kwargs: Any):
    if tool_name != "terminal":
        return None
    sandbox = store.assignment_sandbox(session_id)
    if sandbox is None:
        return {"action": "block", "message": t("super_agent.blocked")}
    return {
        "action": "modify",
        "args": {**args, "sandbox": sandbox, "_host_local": True, "_sandbox_required": True},
    }


def _workspace_root(ctx: Any) -> str:
    raw = str(ctx.get_config("workspace_root", "") or "").strip()
    if not raw:
        raise ValueError(t("super_agent.set_root", plugin_id=ctx.plugin_id))
    root = Path(raw).expanduser()
    if not root.is_absolute() or not root.is_dir():
        raise ValueError(t("super_agent.invalid_root"))
    return str(root.resolve())


def _source_snapshot(source: Any, actor_id: str | None = None) -> dict | None:
    if source is None:
        return None
    if hasattr(source, "to_dict"):
        source = source.to_dict()
    if not isinstance(source, Mapping) or source.get("platform") == "local":
        return None
    snapshot = dict(source)
    if actor_id is not None and actor_id != (source.get("user_id") or ""):
        snapshot["actor_id"] = actor_id
    return snapshot


def _source_instance(source: dict | None, fallback: str) -> str:
    if not source:
        return fallback
    return ":".join(str(source.get(key) or "") for key in ("platform", "chat_id", "thread_id"))


def _allows_message_source(ctx: Any, source: Mapping[str, Any]) -> bool:
    raw = ctx.get_config("message_sources", ["*"])
    if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
        raise ValueError(t("super_agent.invalid_sources"))
    allowed = {item.strip().lower() for item in raw if item.strip()}
    platform = str(source.get("platform") or "").strip().lower()
    return "*" in allowed or platform in allowed


def _parse_new(ctx: Any, text: str) -> tuple[str, str]:
    text = text.strip()
    if not text:
        raise ValueError(t("super_agent.no_task"))
    first, separator, rest = text.partition(" ")
    level = first.lower()
    if level == "full-access":
        raise ValueError(t("super_agent.full_access"))
    if level in SANDBOX_LEVELS:
        if not separator or not rest.strip():
            raise ValueError(t("super_agent.no_sandbox_task"))
        return level, rest.strip()
    return _default_sandbox(ctx), text


def _default_sandbox(ctx: Any) -> str:
    sandbox = str(ctx.get_config("default_sandbox", "workspace-write") or "workspace-write").strip()
    if sandbox not in SANDBOX_LEVELS:
        raise ValueError(t("super_agent.invalid_sandbox"))
    return sandbox


async def _new_ticket(
    ctx: Any,
    runtime: SuperAgentRuntime,
    text: str,
    *,
    source: Any = None,
    source_instance: str = "local",
    external_event_id: str | None = None,
    offer_actor: str | None = None,
) -> str:
    sandbox, task = _parse_new(ctx, text) if offer_actor is None else (_default_sandbox(ctx), text)
    snapshot = _source_snapshot(source, offer_actor)
    ticket, created = await runtime.submit(
        task=task,
        sandbox=sandbox,
        workspace_root=_workspace_root(ctx),
        source_instance=_source_instance(snapshot, source_instance),
        external_event_id=external_event_id or uuid.uuid4().hex,
        source=snapshot,
        offer_actor=offer_actor,
    )
    duplicate = t("super_agent.duplicate") if not created else ""
    paused = await asyncio.to_thread(runtime.store.is_paused)
    waiting = t("super_agent.waiting") if paused else ""
    return t("super_agent.queued", id=ticket['id'], sandbox=ticket['sandbox'], duplicate=duplicate, waiting=waiting)


def _status_text(status: str) -> str:
    return t(f"super_agent.status_{status}")


def _ticket_text(ticket: dict) -> str:
    assignment = ticket.get("assignment") or {}
    lines = [
        f"{ticket['id']}: {_status_text(ticket['status'])} ({ticket['sandbox']})",
        t("super_agent.task", task=ticket['task']),
    ]
    if assignment.get("worker_session_id"):
        lines.append(t("super_agent.session", worker_session_id=assignment['worker_session_id']))
    if assignment.get("workspace"):
        lines.append(t("super_agent.worktree", workspace=assignment['workspace']))
    alert = ticket.get("watchdog_alert")
    if alert:
        lines.extend((t("super_agent.watchdog", detail=alert['detail']), t("super_agent.suggestion", suggestion=alert['suggestion'])))
    lines.append(t("super_agent.budget", retry_count=ticket['retry_count'], limit=MAX_RETRIES))
    if ticket.get("last_error"):
        lines.append(t("super_agent.error", last_error=ticket['last_error']))
    if ticket.get("result"):
        lines.append(t("super_agent.result", result=ticket['result']))
    return "\n".join(lines)


async def _router_command(runtime: SuperAgentRuntime, command: str) -> str | None:
    if command == "pause":
        changed = await runtime.pause()
        return t("super_agent.paused") if changed else t("super_agent.already_paused")
    if command == "resume":
        changed = await runtime.resume()
        return t("super_agent.resumed") if changed else t("super_agent.already_running")
    if command in {"health", "watchdog"}:
        state = await asyncio.to_thread(runtime.store.router_state)
        counts = await asyncio.to_thread(runtime.store.counts)
        alerts = await asyncio.to_thread(runtime.store.list_watchdog_alerts)
        events = await asyncio.to_thread(runtime.store.list_control_events)
        threshold = getattr(runtime, "watchdog_seconds", DEFAULT_WATCHDOG_SECONDS)
        lines = [
            t("super_agent.router", state=t("super_agent.state_paused") if state["paused"] else t("super_agent.state_running")),
            t("super_agent.health", count=len(alerts), threshold=threshold),
            t("super_agent.tickets") + (
                ", ".join(f"{_status_text(key)}={value}" for key, value in sorted(counts.items())) or t("super_agent.none")
            ),
        ]
        lines.extend(
            t("super_agent.alert", ticket_id=alert['ticket_id'], detail=alert['detail'], suggestion=alert['suggestion'])
            for alert in alerts
        )
        if events:
            lines.append(t("super_agent.controls") + "; ".join(
                f"{event['action']} {event['ticket_id'] or 'router'}" for event in events
            ))
        return "\n".join(lines)
    if command == "list":
        tickets = await asyncio.to_thread(runtime.store.list_tickets)
        state = await asyncio.to_thread(runtime.store.router_state)
        alerts = await asyncio.to_thread(runtime.store.list_watchdog_alerts)
        header = (
            t("super_agent.list", state=t("super_agent.state_paused") if state["paused"] else t("super_agent.state_running"), count=len(alerts))
        )
        return "\n".join([header, *(_ticket_text(ticket).splitlines()[0] for ticket in tickets)])
    return None


async def _ticket_command(
    runtime: SuperAgentRuntime, command: str, ticket_id: str, value: str, source_instance: str,
) -> str:
    if command == "status":
        ticket = await asyncio.to_thread(runtime.store.get_ticket, ticket_id)
        if ticket is None:
            return t("super_agent.unknown", ticket_id=ticket_id)
        events = await asyncio.to_thread(runtime.store.list_control_events, ticket_id)
        text = _ticket_text(ticket)
        if events:
            text += t("super_agent.recovery") + "; ".join(event["action"] for event in events)
        return text
    if command == "tell":
        if not value.strip():
            return t("super_agent.usage_tell")
        return (
            t("super_agent.followup", ticket_id=ticket_id)
            if await runtime.tell(ticket_id, value)
            else t("super_agent.terminal", ticket_id=ticket_id)
        )
    if command in {"stop", "cancel"}:
        return (
            t("super_agent.cancelled", ticket_id=ticket_id)
            if await runtime.cancel(ticket_id) is not None
            else t("super_agent.terminal", ticket_id=ticket_id)
        )
    if command == "takeover":
        return (
            t("super_agent.takeover", ticket_id=ticket_id)
            if await runtime.takeover(ticket_id) is not None
            else t("super_agent.terminal", ticket_id=ticket_id)
        )
    if command == "retry":
        return (
            t("super_agent.retry", ticket_id=ticket_id)
            if await runtime.retry(ticket_id)
            else t("super_agent.cannot_retry", ticket_id=ticket_id, limit=MAX_RETRIES)
        )
    if command == "subscribe":
        thread_key, _, mode = value.strip().partition(" ")
        if not thread_key:
            return t("super_agent.usage_subscribe")
        mode = (mode.strip() or "follow").lower()
        if mode not in ("follow", "context"):
            return t("super_agent.bad_subscription")
        result = await runtime.subscribe(ticket_id, source_instance, thread_key, mode)
        if result["accepted"]:
            return t("super_agent.subscribed", ticket_id=ticket_id, thread_key=thread_key, mode=mode)
        return t("super_agent.cannot_subscribe", ticket_id=ticket_id)
    if command == "unsubscribe":
        thread_key = value.strip()
        if not thread_key:
            return t("super_agent.usage_unsubscribe")
        removed = await runtime.unsubscribe(ticket_id, source_instance, thread_key)
        return (
            t("super_agent.unsubscribed", ticket_id=ticket_id, thread_key=thread_key)
            if removed else t("super_agent.no_subscription", ticket_id=ticket_id, thread_key=thread_key)
        )
    if command == "handback":
        trigger_id, _, reason = value.strip().partition(" ")
        if not trigger_id or not reason.strip():
            return t("super_agent.usage_handback")
        recorded = await runtime.handback(ticket_id, trigger_id, reason.strip())
        return (
            t("super_agent.handback", trigger_id=trigger_id, ticket_id=ticket_id)
            if recorded else t("super_agent.cannot_handback", ticket_id=ticket_id)
        )
    return t("super_agent.help")


async def _command(
    ctx: Any,
    runtime: SuperAgentRuntime,
    raw_args: str,
    *,
    source: Any = None,
    session_key: str = "",
    actor_id: str | None = None,
    **_kwargs: Any,
) -> str:
    command, _, rest = raw_args.strip().partition(" ")
    command = command.lower()
    if command in {"", "help", "-h", "--help"}:
        return t("super_agent.help") + "\n\n" + t("super_agent.chat_help")
    if command == "new":
        try:
            return await _new_ticket(
                ctx, runtime, rest, source=_source_snapshot(source, actor_id), source_instance=f"slash:{session_key or 'local'}"
            )
        except (OSError, ValueError) as exc:
            return t("super_agent.rejected", exc=exc)
    source_instance = f"gateway:{session_key}" if _source_snapshot(source) else "slash"
    if command == "observe":
        thread_key, _, mode = rest.strip().partition(" ")
        if not thread_key:
            return t("super_agent.usage_observe")
        mode = (mode.strip() or "observe").lower()
        if mode not in ("off", "observe", "auto"):
            return t("super_agent.bad_mode")
        thread = await asyncio.to_thread(runtime.store.ensure_thread, source_instance, thread_key, mode)
        changed = await asyncio.to_thread(runtime.store.set_thread_accept_mode, thread["id"], mode)
        action = t("super_agent.mode_changed", mode=mode) if changed else t("super_agent.mode_unchanged", mode=mode)
        return t("super_agent.thread_mode", thread_key=thread_key, action=action)
    router_reply = await _router_command(runtime, command)
    if router_reply is not None:
        return router_reply
    ticket_id, _, value = rest.strip().partition(" ")
    if not ticket_id:
        return t("super_agent.help")
    return await _ticket_command(runtime, command, ticket_id, value, source_instance)


async def _post_gateway_admission(
    ctx: Any,
    runtime: SuperAgentRuntime,
    *,
    text: str,
    source: dict,
    message_id: str = "",
    session_key: str = "",
    message_type: str = "text",
    actor_id: str | None = None,
    human_chat: bool = True,
    **_kwargs: Any,
):
    if message_type != "text" or not text.strip() or text.lstrip().startswith("/") or not _source_snapshot(source):
        return None
    prefix, separator, task = text.partition(":")
    is_sa = separator and prefix.strip().lower() == "sa"
    try:
        if not _allows_message_source(ctx, source):
            return None
        if is_sa:
            reply = await _new_ticket(ctx, runtime, task, source=_source_snapshot(source, actor_id), source_instance=f"gateway:{session_key}",
                                      external_event_id=message_id)
        else:
            if not human_chat:
                return None
            from .chat import route_chat
            chat = {"text": text, "source": source, "session_key": session_key, "message_id": message_id,
                    "actor_id": (source.get("user_id") or "") if actor_id is None else actor_id}
            reply = await route_chat(ctx, runtime, chat, action="inbound")
            if reply is None:
                return None
    except Exception as exc:
        logger.exception("Super Agent gateway admission failed")
        reply = t("super_agent.rejected", exc=exc)
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
    from .chat import register_chat_tool
    register_chat_tool(ctx, runtime)
    ctx.register_hook(
        "post_gateway_admission",
        lambda **kwargs: _post_gateway_admission(ctx, runtime, **kwargs),
    )
    ctx.register_command(
        "super-agent",
        lambda raw_args, **kwargs: _command(ctx, runtime, raw_args, **kwargs),
        description=t("super_agent.description"),
        args_hint="<new|status|list|tell|stop|retry|takeover|pause|resume|health> ...",
        argument_mode="text",
    )
    ctx.on_unload(runtime.stop)
    try:
        runtime.start()
    except RuntimeError:
        pass
