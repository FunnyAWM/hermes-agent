"""``post_gateway_admission``: let a plugin consume an admitted inbound message (#129958).

``GatewayInboundMixin._handle_message`` fires it once per non-internal message that survived
authorization, bot admission, pause/drain, pending-reply intercepts, the running-session lane and
idle slash-command dispatch, inside the claimed session slot. It runs under the message's ROUTED
profile scope: the receiving bot's handler binds the transport home (auth reads its ``.env``), which
differs from the runtime home when that bot serves a chat for another profile.

Fail-open: a callback that raises, times out or returns anything other than
``{"action": "handled"}`` is logged/ignored and the message proceeds to the agent, so one buggy
plugin can never blackhole a profile's traffic. The payload is a plain snapshot, never live
runner/session-store handles.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any, Optional, Tuple

logger = logging.getLogger(__name__)

POST_ADMISSION_HOOK = "post_gateway_admission"


def _routed_scope(runner: Any, source: Any):
    if not getattr(getattr(runner, "config", None), "multiplex_profiles", False):
        return contextlib.nullcontext()
    from gateway.run import _async_profile_runtime_scope
    return _async_profile_runtime_scope(runner._resolve_profile_home_for_source(source))


async def run_post_admission_hook(
    runner: Any, event: Any, source: Any, session_key: str
) -> tuple[bool, Optional[str]]:
    """Return ``(handled, reply)``; ``handled=False`` means run the ordinary agent turn."""
    try:
        async with _routed_scope(runner, source):
            from hermes_cli.lifecycle import ainvoke_hook, has_hook
            if not has_hook(POST_ADMISSION_HOOK):
                return False, None
            from gateway.session_context import is_human_chat_input
            results = await ainvoke_hook(
                POST_ADMISSION_HOOK, session_key=session_key,
                platform=source.platform.value if source.platform else "",
                source=source.to_dict(), message_id=event.message_id, text=event.text or "",
                message_type=event.message_type.value, human_chat=is_human_chat_input(event),
                actor_id=event.sender_id(),
            )
            for result in results:
                if isinstance(result, dict) and result.get("action") == "handled":
                    reply = result.get("reply")
                    if result.get("record_exchange") is True and isinstance(reply, str) and reply.strip():
                        # A failed history write must never send a consumed task to another agent.
                        try:
                            await record_channel_exchange(runner, event, reply)
                        except Exception:
                            logger.exception("Could not record the handled exchange for %s", session_key)
                    return True, reply if isinstance(reply, str) and reply.strip() else None
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("%s failed; continuing with the agent turn", POST_ADMISSION_HOOK, exc_info=True)
        return False, None
    return False, None


def _append_exchange(store, sid, event, reply):
    from persistence.conversation.messages import NewMessage
    if event.message_id and store.messages.has_platform_message_id(sid, event.message_id):
        return
    store.messages.append_batch(sid, [
        NewMessage(role="user", content=event.text, platform_message_id=event.message_id),
        NewMessage(role="assistant", content=reply),
    ])


async def record_channel_exchange(runner, event, reply, entry=None):
    sessions = runner.session_store
    owner = entry or await asyncio.to_thread(sessions.get_or_create_session, event.source)
    leases = getattr(runner, "_turn_leases", None)
    token = None

    def persist():
        with sessions._get_transcript_drain_lock():
            sid = sessions._compression_tip_for_session_id(owner.session_id)
            store = sessions._persistence_for_key(owner.session_key)
            _append_exchange(store, sid, event, reply)

    try:
        if leases is not None:
            token = await leases.acquire(owner.session_id, owner_key=owner.session_key, generation=0)
        await asyncio.to_thread(persist)
    finally:
        if token is not None:
            leases.release(token)


def is_bot_chat(store, session_id):
    from tools.bot_mode_probe import BOT_CHAT_TITLE
    return (store.sessions.get_title(session_id) == BOT_CHAT_TITLE
            or store.sessions.resolve_by_title(BOT_CHAT_TITLE) == session_id)


async def route_bot_chat(authority, ref, event, policy) -> str | None:
    """Offer a claimed viewer prompt in the canonical Bot Chat to interested plugins."""
    from gateway.session_context import is_human_chat_input
    from hermes_cli.lifecycle import ainvoke_hook, has_hook, hook_known_unused

    hook = "pre_bot_chat"
    if not isinstance(event.text, str) or not is_human_chat_input(event) or hook_known_unused(hook):
        return None
    from gateway.session_persistence import authority_persistence
    store = authority_persistence(authority)

    def bot_toolsets():
        if is_bot_chat(store, ref.session_id) and has_hook(hook):
            return policy.current_toolsets(policy.config(authority, secrets=False))
        return None

    toolsets = await asyncio.to_thread(bot_toolsets)
    if toolsets is None:
        return None
    live = authority.sessions[ref.session_id]
    results = await ainvoke_hook(hook, text=event.text, message_id=event.message_id,
                                 session_key=live.route, actor_id=event.sender_id() or "",
                                 source=event.source.to_dict(), toolsets=list(toolsets))
    for result in results:
        if not isinstance(result, dict):
            continue
        if result.get("action") == "handled" and isinstance(reply := result.get("reply"), str):
            from gateway.session_runtime_workers import tracked_write
            if reply and result.get("record_exchange", True):
                await tracked_write(authority, lambda: _append_exchange(store, authority.physical_target(ref), event, reply))
            return reply
        if isinstance(note := result.get("context"), str) and note.strip():
            event._bot_chat_notes = (*getattr(event, "_bot_chat_notes", ()), note)
    return None
