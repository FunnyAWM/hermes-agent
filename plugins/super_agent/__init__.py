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
from hermes_cli.plugin_command_dispatch import PluginNotice

from .runtime import DEFAULT_WATCHDOG_SECONDS, SuperAgentRuntime
from .sources import MessageSources
from .store import MAX_RETRIES, SANDBOX_LEVELS, SuperAgentStore
from .workspace import task_repository

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


def _workspace_root(ctx: Any, task: str) -> str:
    raw = str(ctx.get_config("workspace_root", "") or "").strip()
    if not raw:
        raise ValueError(t("super_agent.set_root", plugin_id=ctx.plugin_id))
    root = Path(raw).expanduser()
    if not root.is_absolute() or not root.is_dir():
        raise ValueError(t("super_agent.invalid_root"))
    return str(task_repository(root.resolve(), task))


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


def _parse_new(ctx: Any, text: str) -> tuple[str, str]:
    text = text.strip()
    if not text:
        raise ValueError(t("super_agent.no_task"))
    first, *parts = text.split(maxsplit=1)
    level = first.lower()
    if level == "full-access":
        raise ValueError(t("super_agent.full_access"))
    if level in SANDBOX_LEVELS:
        if not parts:
            raise ValueError(t("super_agent.no_sandbox_task"))
        return level, parts[0]
    return _default_sandbox(ctx), text


def _default_sandbox(ctx: Any) -> str:
    sandbox = str(ctx.get_config("default_sandbox", "workspace-write") or "workspace-write").strip()
    if sandbox not in SANDBOX_LEVELS:
        raise ValueError(t("super_agent.invalid_sandbox"))
    return sandbox


def _has_destination(ctx: Any, destination: dict | None) -> bool:
    permissions = MessageSources.from_config(ctx)
    if not ctx.gateway_injection_allowed():
        return False
    bot_chat = bool(destination and destination.get("bot_chat"))
    writable = bool(destination) and permissions.writes(destination["platform"])
    if writable and not bot_chat:
        return True
    try:
        platforms = ctx.home_channel_platforms(require_gateway=True)
        if writable and bot_chat:
            # A Desktop Bot Chat is delivered through this profile's canonical Bot Chat only,
            # which is exactly what makes local a home platform.
            return "local" in platforms
        return any(permissions.writes(p) and not permissions.reads(p) for p in platforms)
    except Exception:
        logger.exception("Could not discover writable Super Agent destinations")
        return False


async def _new_ticket(
    ctx: Any,
    runtime: SuperAgentRuntime,
    text: str,
    *,
    source: Any = None,
    source_instance: str = "local",
    external_event_id: str | None = None,
    offer_actor: str | None = None,
    answer_id: str = "",
    bot_chat: bool = False,
    natural: bool = False,
) -> str:
    sandbox, task = _parse_new(ctx, text) if offer_actor is None and not natural else (_default_sandbox(ctx), text)
    snapshot = _source_snapshot(source, offer_actor)
    local_id = ((source or {}).get("chat_id") if (source or {}).get("platform") == "local"
                and (offer_actor or source.get("user_id")) else None)
    destination = snapshot
    if bot_chat or local_id:
        destination = {"platform": "local", "actor_id": offer_actor or (source or {}).get("user_id", ""),
                       **({"bot_chat": True} if bot_chat else {"local_session_id": local_id})}
    has_destination = await asyncio.to_thread(_has_destination, ctx, destination)
    paused = await asyncio.to_thread(runtime.store.is_paused)
    ticket, created = await runtime.submit(
        task=task,
        sandbox=sandbox,
        workspace_root=await asyncio.to_thread(_workspace_root, ctx, task),
        source_instance=_source_instance(snapshot, source_instance),
        external_event_id=external_event_id or uuid.uuid4().hex,
        source=destination,
        offer_actor=offer_actor,
        answer_id=answer_id,
    )
    if ticket.get("retired"):
        return PluginNotice(t("super_agent.retired", id=ticket["id"]), "super_agent.queued." + ticket["id"])
    duplicate = t("super_agent.duplicate") if not created else ""
    waiting = t("super_agent.waiting") if paused else ""
    message = t("super_agent.queued", id=ticket['id'], sandbox=ticket['sandbox'], duplicate=duplicate, waiting=waiting)
    if not has_destination:
        message += " " + t("super_agent.no_destination")
    return PluginNotice(message, "super_agent.queued." + ticket["id"])


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
    if ticket.get("notice_denied"):
        lines.append(t("super_agent.notice_denied"))
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
        if error := getattr(runtime, "scheduler_error", None):
            lines.append(t("super_agent.scheduler_error", error=error))
        stopping = await asyncio.to_thread(runtime.store.pending_stops)
        lines.extend(t("super_agent.stop_pending", id=tid) for tid in dict.fromkeys(s["ticket_id"] for s in stopping))
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


async def _ticket_status(runtime: SuperAgentRuntime, ticket_id: str) -> str:
    ticket = await asyncio.to_thread(runtime.store.get_ticket, ticket_id)
    if ticket is None:
        return t("super_agent.unknown", ticket_id=ticket_id)
    events = await asyncio.to_thread(runtime.store.list_control_events, ticket_id)
    text = _ticket_text(ticket)
    details = await asyncio.to_thread(runtime.store.ticket_details, ticket_id)
    subscriptions = await asyncio.to_thread(runtime.store.list_subscriptions, ticket_id)
    for sub in subscriptions:
        text += "\n" + t("super_agent.subscription_row", thread=sub["thread_key"], mode=sub["mode"])
    for row in details["handbacks"]:
        text += "\n" + t("super_agent.handback_row", trigger=row["trigger_id"], reason=row["reason"])
    unavailable = t("super_agent.unavailable")
    for row in details["turns"]:
        metrics = row["metrics"]
        values = {key: metrics[key] if metrics.get(key) is not None else unavailable
                  for key in ("duration_seconds", "input_tokens", "output_tokens", "cache_read_tokens")}
        text += "\n" + t("super_agent.turn_row", generation=row["generation"], sequence=row["sequence"],
                          result=row["result"] or unavailable, **values)
    for row in ticket.get("deliveries", ()):
        text += "\n" + t("super_agent.delivery_row", id=row["id"], target=row["target"],
                          status=row["status"], error=row["error"] or "")
    if events:
        text += t("super_agent.recovery") + "; ".join(event["action"] for event in events)
    return text


async def _ticket_command(
    runtime: SuperAgentRuntime, command: str, ticket_id: str, value: str, source_instance: str,
) -> str:
    if command == "status":
        return await _ticket_status(runtime, ticket_id)
    if command == "redeliver":
        if value and not value.strip().isdecimal():
            return t("super_agent.usage_redeliver")
        count = await asyncio.to_thread(runtime.store.redeliver, ticket_id, int(value) if value else None)
        if count:
            runtime.wake()
        return t("super_agent.redelivered", count=count)
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


async def _handoff_command(ctx, runtime, command, rest, *, source, session_key, actor_id, bot_chat, message_id):
    if command == "new":
        reply = await _new_ticket(
            ctx, runtime, rest, source=_source_snapshot(source, actor_id) or source,
            source_instance=f"slash:{session_key or 'local'}", external_event_id=message_id, bot_chat=bot_chat,
        )
        return True, reply
    ticket_id, _, value = rest.strip().partition(" ")
    if not ticket_id:
        return False, t("super_agent.help")
    if command == "tell":
        if not value.strip():
            return False, t("super_agent.usage_tell")
        delivery = f"slash:{session_key}:{message_id}" if message_id else ""
        ok = await runtime.tell(ticket_id, value, message_id=delivery)
        return ok, t("super_agent.followup" if ok else "super_agent.terminal", ticket_id=ticket_id)
    ok = await runtime.retry(ticket_id)
    return ok, t("super_agent.retry" if ok else "super_agent.cannot_retry", ticket_id=ticket_id, limit=MAX_RETRIES)


async def _command(
    ctx: Any,
    runtime: SuperAgentRuntime,
    raw_args: str,
    *,
    source: Any = None,
    session_key: str = "",
    actor_id: str | None = None,
    record_exchange=None,
    bot_chat: bool = False,
    message_id: str = "",
    **_kwargs: Any,
) -> str:
    command, _, rest = raw_args.strip().partition(" ")
    command = command.lower()
    if command in {"", "help", "-h", "--help"}:
        return t("super_agent.help") + "\n\n" + t("super_agent.chat_help")
    if hasattr(source, "to_dict"):
        source = source.to_dict()
    if command in {"new", "tell", "retry", "subscribe", "observe"}:
        platform = (source or {}).get("platform", "local")
        try:
            permissions = MessageSources.from_config(ctx)
            if not permissions.reads(platform):
                return t("super_agent.chat_disabled")
            if command in {"new", "tell", "retry"}:
                ok, reply = await _handoff_command(ctx, runtime, command, rest, source=source, session_key=session_key,
                                                   actor_id=actor_id, bot_chat=bot_chat, message_id=message_id)
                writable = permissions.writes(platform)
                if ok and writable and record_exchange:
                    await record_exchange(reply)
                if ok and not writable and not isinstance(reply, PluginNotice):
                    return ""
                return reply
        except (OSError, ValueError) as exc:
            return t("super_agent.rejected", exc=exc)
    source_instance = f"gateway:{session_key}" if _source_snapshot(source) else "slash"
    if command == "observe-log":
        key = rest.strip()
        snapshot = _source_snapshot(source)
        if not key or (snapshot and key != _source_instance(snapshot, "")):
            return t("super_agent.usage_observe_log")
        rows = await asyncio.to_thread(runtime.store.thread_observations, source_instance, key)
        return "\n".join(f"{row['action']}: {row['detail']}" for row in rows) or t("super_agent.none")
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
    if command in {"status", "redeliver"} and (snapshot := _source_snapshot(source)):
        visible = await asyncio.to_thread(runtime.store.chat_tickets, _source_instance(snapshot, ""),
                                          actor_id or snapshot.get("user_id", ""), ticket_id)
        if not visible:
            return t("super_agent.unknown", ticket_id=ticket_id)
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
    from .chat import admit_chat
    return await admit_chat(ctx, runtime, text=text, source=source, session_key=session_key, message_id=message_id,
                            actor_id=actor_id if actor_id is not None else source.get("user_id") or "", human_chat=human_chat)


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
        args_hint="<new|status|list|tell|stop|retry|takeover|redeliver|observe-log|pause|resume|health> ...",
        argument_mode="text",
    )
    ctx.on_unload(runtime.stop)
    try:
        runtime.start()
    except RuntimeError:
        pass
