"""Record acknowledged plugin reports in their actual IM conversation between turns."""
from __future__ import annotations

import asyncio
import json
import logging

from persistence_assembly import open_persistence, profile_scope

logger = logging.getLogger(__name__)


def _threads(route):
    thread = route.thread_id
    return (str(thread),) if thread is not None else ("1", "", str(route.chat_id))


def _home_session(store, route):
    for thread in _threads(route):
        sid = store.routing.session_for_origin(platform=route.platform.value, chat_id=str(route.chat_id),
                                              thread_id=thread, user_id=route.user_id)
        if sid:
            return sid
    return None


def _write(home, checkpoint, runner, route, origin_key, identity, text):
    with open_persistence(profile_scope(home=home)) as store:
        if "session_id" not in checkpoint:
            checkpoint["session_id"] = (runner.session_store.peek_session_id(origin_key) if origin_key
                                        else _home_session(store, route))
        sid = checkpoint["session_id"]
        if not sid:
            return
        row = store.sessions.get(sid)
        if row is None:
            return  # An explicitly deleted conversation stays deleted.
        origin = row.origin or {}
        stored_scope = origin.get("scope_id", origin.get("guild_id"))
        matches = (row.source == route.platform.value and str(row.chat_id) == str(route.chat_id)
                   and str(row.thread_id or "") in _threads(route)
                   and (not origin_key or row.session_key == origin_key)
                   and (origin_key or not route.user_id or str(row.user_id) == str(route.user_id))
                   and (not getattr(route, "scope_id", None) or stored_scope == route.scope_id))
        if not matches:
            return  # The checkpoint cannot grant a write to a different conversation.
        store.messages.append_delivery_report(store.lineage.tip(sid), identity, text)


async def with_transcript(ctx, runner, route, text, delivery_id, checkpoint, send, *, origin_key=None):
    """A confirmed send that hit a busy transcript is retried only on the persistence side."""
    destination = [route.platform.value, str(route.chat_id), route.thread_id, route.user_id,
                   getattr(route, "scope_id", None), origin_key]
    identity = json.dumps([ctx.plugin_id, delivery_id, destination], separators=(",", ":"))
    home = ctx._manager.home_path
    if not isinstance(checkpoint, dict) or checkpoint.get("identity") != identity:
        result = await send()
        if not result.get("ok"):
            return result
        checkpoint = {"identity": identity, "message_id": result.get("message_id")}
    else:
        checkpoint = dict(checkpoint)
    try:
        await asyncio.to_thread(_write, home, checkpoint, runner, route, origin_key, identity, text)
    except Exception:
        logger.warning("Plugin report %s is awaiting transcript persistence", delivery_id, exc_info=True)
        return {"ok": False, "error": "history_pending", "delivery_checkpoint": checkpoint,
                "message_id": checkpoint.get("message_id")}
    return {"ok": True, "message_id": checkpoint.get("message_id")}
