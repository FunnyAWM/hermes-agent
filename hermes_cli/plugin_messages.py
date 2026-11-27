"""``PluginContext.post_message``: one message in the current conversation, editable in place.

The conversation is the session bound in the calling context (a tool or hook running in its turn,
its slash command, a hook fired for it): the plugin never names a chat, so it cannot reach another
one. Outside any session (a timer, a plugin thread, cron, a process with no live gateway) the call
is refused with a result, never an exception. Routing and the edit fallback live in
``gateway/plugin_messages.py``; this side only hands work to the gateway loop and back.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from functools import partial
from typing import Any, Mapping, Optional

logger = logging.getLogger("hermes_cli.plugins")

_TIMEOUT_S = 30.0


def _err(code: str, detail: str = "") -> dict[str, Any]:
    return {"ok": False, "error": code, **({"detail": detail} if detail else {})}


class PluginMessage:
    """Handle for one posted message. ``ok`` / ``message_id`` / ``error`` describe the last
    operation; ``edit(text)`` replaces the text in place (or posts anew where the surface cannot
    edit, and the handle then follows the new message). A refused post returns a handle whose
    edits are refused the same way. Operations stay ordered: one the gateway has not finished
    (``error == "timeout"``, ``pending`` set) is waited for before the next runs, and its outcome
    still lands on the handle, so a slow post never leaves an orphan message behind a dead handle."""

    def __init__(self, plugin_id: str, target: Any, result: dict[str, Any]):
        self._plugin_id, self._target = plugin_id, target
        self.result = result
        self.message_id: Optional[str] = result.get("message_id")
        self._pending: Optional[concurrent.futures.Future] = None

    @property
    def ok(self) -> bool:
        return bool(self.result.get("ok"))

    @property
    def error(self) -> Optional[str]:
        return self.result.get("error")

    @property
    def pending(self) -> bool:
        return self._pending is not None and not self._pending.done()

    def __repr__(self) -> str:
        return f"<PluginMessage ok={self.ok} message_id={self.message_id!r} error={self.error!r}>"

    def _adopt(self, other: PluginMessage) -> None:
        self._target, self.result, self.message_id = other._target, other.result, other.message_id

    def _refused(self) -> Optional[dict[str, Any]]:
        if self._target is None:
            return _err(self.error or ("not_editable" if self.ok else "no_conversation"), self.result.get("detail", ""))
        return None

    def _settle(self, result: dict[str, Any]) -> dict[str, Any]:
        self.result = result
        if result.get("ok") and result.get("message_id"):
            self.message_id = result["message_id"]
        return result

    async def _edit(self, text: str) -> dict[str, Any]:
        refused = self._refused()
        if refused is not None:
            return refused
        from gateway import plugin_messages
        return self._settle(await plugin_messages.edit(self._target, self.message_id, str(text),
                                                       plugin_id=self._plugin_id))

    async def aedit(self, text: str) -> dict[str, Any]:
        """``edit`` for code already running on the gateway's event loop (async hooks/commands)."""
        if self.pending:
            await asyncio.wrap_future(self._pending)
        return await self._edit(text)

    def edit(self, text: str) -> dict[str, Any]:
        """Replace the message text: ``{"ok", "message_id", "edited", ["fallback"]}``. Thread-safe."""
        if _on_gateway_loop():  # never block the loop the pending operation needs
            return _err("on_event_loop", "called from the gateway event loop: use await handle.aedit(...)")
        if self.pending:
            try:
                self._pending.result(_TIMEOUT_S)
            except concurrent.futures.TimeoutError:
                return _err("timeout", "the previous operation on this message has not finished")
            except Exception:  # its own outcome is already on the handle
                logger.debug("plugin_message: previous operation failed", exc_info=True)
        refused = self._refused()
        if refused is not None:
            return refused
        return _run_on_gateway_loop(self._edit(text), self)


def _gateway_loop():
    try:
        from gateway.run import _gateway_runner_ref
        runner = _gateway_runner_ref()
    except ImportError:
        runner = None
    loop = getattr(runner, "_gateway_loop", None)
    if runner is None or not getattr(runner, "_running", False) or loop is None or loop.is_closed():
        return None, None
    return runner, loop


def _profile_homes(ctx, runner):
    profile = runner._served_profile_for_home(ctx._manager.home_path)
    if profile is None:
        return {}
    primary = getattr(runner, "_primary_profile_name", None) or "default"
    return {platform.value: (name, platform, cfg.home_channel) for name, platform, cfg in runner._served_home_channel_configs()
            if (name or primary) == profile and cfg.enabled and cfg.home_channel and cfg.home_channel.chat_id}


def _local_bot_chat(ctx, runner):
    """(owning authority, its canonical Bot Chat row) for this plugin's profile, else (None, None).

    The one read behind both answers about ``local``: whether it is a destination
    (``home_platforms``) and where a local delivery lands (``post_home``). A profile holding an
    authority but no canonical Bot Chat accepts a local write grant yet can deliver nothing."""
    from gateway.session_authorities import authority_for_home
    from gateway.session_persistence import authority_persistence
    from tools.bot_mode_probe import BOT_CHAT_TITLE
    owner = authority_for_home(runner, ctx._manager.home_path)
    if owner is None:
        return None, None
    return owner, authority_persistence(owner).sessions.get_by_title(BOT_CHAT_TITLE)


def home_platforms(ctx, *, require_gateway: bool = False) -> list[str]:
    """Home platforms of this plugin's profile; ``local`` counts only while the profile's own
    authority holds its canonical Bot Chat, the row ``post_home`` delivers a local message into."""
    runner, _ = _gateway_loop()
    if runner is None:
        if require_gateway:
            raise RuntimeError("gateway_unavailable")
        return []
    platforms = list(_profile_homes(ctx, runner))
    if _local_bot_chat(ctx, runner)[1] is not None:
        platforms.append("local")
    return platforms


async def _send_home(runner, target, text):
    for name, key, _cfg, home, transport in runner._served_home_channel_transports():
        if (name, key, home) == target:
            sent, message_id = await runner._send_home_channel_receipt(
                key, home, transport, text, "Plugin home delivery to %s/%s failed: %s")
            return {"ok": True, "message_id": message_id} if sent else _err("send_failed")
    return _err("home_channel_unavailable")


async def post_home(ctx, text: str, platform: str, delivery_id: str | None = None, checkpoint=None) -> PluginMessage:
    runner, _ = _gateway_loop()
    result = _err("home_channel_unavailable")
    if runner is None:
        result = _err("gateway_unavailable")
    elif not await asyncio.to_thread(ctx.gateway_injection_allowed):
        result = _err("permission_denied")
    elif platform == "local":
        from gateway.session_cron import admit_local_delivery
        owner, row = await asyncio.to_thread(_local_bot_chat, ctx, runner)
        if row is not None:
            identity = [ctx.plugin_id, delivery_id] if delivery_id else None
            sent = await admit_local_delivery(owner, row.session_id, text, {}, producer="plugin_notice",
                                              delivery_id=identity)
            result = {"ok": True} if sent else _err("home_channel_unavailable")
    elif target := _profile_homes(ctx, runner).get(platform):
        send = partial(_send_home, runner, target, text)
        if delivery_id:
            from gateway.plugin_reports import with_transcript
            result = await with_transcript(ctx, runner, target[2], text, delivery_id, checkpoint, send)
        else:
            result = await send()
    return PluginMessage(ctx.plugin_id, None, result)


async def post_local_session(ctx, text: str, session_id: str, actor_id: str, delivery_id: str) -> PluginMessage:
    from gateway.owner_identity import same_owner
    from gateway.session_authorities import authority_for_home
    from gateway.session_cron import admit_local_delivery
    from gateway.session_local_recovery import owned_receipt
    from gateway.session_persistence import authority_persistence
    from persistence.authority.vocabulary import LEGACY_LOCAL_SOURCES
    from persistence.core.errors import AuthorityRefusal

    runner, _ = _gateway_loop()
    owner = authority_for_home(runner, ctx._manager.home_path) if runner else None
    if owner is None:
        return PluginMessage(ctx.plugin_id, None, _err("gateway_unavailable"))
    if not await asyncio.to_thread(ctx.gateway_injection_allowed):
        return PluginMessage(ctx.plugin_id, None, _err("permission_denied"))
    if not all(isinstance(value, str) and value.strip() for value in (session_id, actor_id, delivery_id)):
        return PluginMessage(ctx.plugin_id, None, _err("invalid_source"))
    row = await asyncio.to_thread(authority_persistence(owner).sessions.get, session_id)
    if row is None or row.source not in LEGACY_LOCAL_SOURCES:
        return PluginMessage(ctx.plugin_id, None, _err("invalid_source"))
    try:
        _, source, _, _ = await asyncio.to_thread(owned_receipt, owner, session_id)
        if not same_owner(source.user_id, actor_id):
            return PluginMessage(ctx.plugin_id, None, _err("permission_denied"))
        sent = await admit_local_delivery(owner, session_id, text, {}, producer="plugin_notice",
                                          delivery_id=[ctx.plugin_id, delivery_id])
    except AuthorityRefusal as exc:
        return PluginMessage(ctx.plugin_id, None, _err(exc.reason))
    except (ValueError, KeyError):
        return PluginMessage(ctx.plugin_id, None, _err("invalid_source"))
    return PluginMessage(ctx.plugin_id, None, {"ok": True} if sent else _err("no_conversation"))


def _on_gateway_loop() -> bool:
    _runner, loop = _gateway_loop()
    try:
        return loop is not None and asyncio.get_running_loop() is loop
    except RuntimeError:
        return False


def _run_on_gateway_loop(coro, handle: PluginMessage) -> dict[str, Any]:
    """Run *coro* on the gateway loop and wait up to ``_TIMEOUT_S``; past that it keeps running as
    the handle's pending operation (its result still lands on the handle)."""
    _runner, loop = _gateway_loop()
    if loop is None:
        coro.close()
        return _err("gateway_unavailable", "no live gateway is running in this process")
    if _on_gateway_loop():
        coro.close()
        return _err("on_event_loop", "called from the gateway event loop: use the async form (await ...)")
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    handle._pending = future
    try:
        return future.result(_TIMEOUT_S)
    except concurrent.futures.TimeoutError:
        result = _err("timeout", f"the gateway did not answer within {_TIMEOUT_S:.0f}s; the operation "
                                 "is still running and its result will land on this handle")
        handle.result = result
        return result
    except Exception as exc:  # the plugin gets a result, never the raise
        logger.warning("plugin_message: gateway hand-off failed", exc_info=True)
        return _err("send_failed", str(exc)[:512])


_NO_SESSION = ("no session is bound in the calling context (a tool call, slash command or hook "
               "fired for a session)")


async def _post_for(ctx: Any, text: str, session_key: str, route: Any, *, editable: bool = True,
                    directed: bool = False) -> PluginMessage:
    plugin_id = ctx.plugin_id
    runner, _loop = _gateway_loop()
    result: dict[str, Any]
    if not session_key:
        result = _err("no_conversation", _NO_SESSION)
    elif runner is None:
        result = _err("gateway_unavailable", "no live gateway is running in this process")
    else:
        from gateway import plugin_messages
        # The session store read is disk-bound: keep it off the gateway loop.
        target, refusal = await asyncio.to_thread(plugin_messages.resolve, runner, session_key=session_key,
                                                  route=route, plugin_home=ctx._manager.home_path, plugin_id=plugin_id,
                                                  directed=directed)
        if target is None:
            result = refusal or _err("no_conversation")
        else:
            result = await plugin_messages.post(target, str(text), plugin_id=plugin_id, editable=editable)
            if result.get("ok"):
                return PluginMessage(plugin_id, target if editable else None, result)
    logger.info("plugin_message plugin=%s op=post refused: %s", plugin_id, result.get("error"))
    return PluginMessage(plugin_id, None, result)


def _directed_route(runner: Any, source: Mapping[str, Any]) -> tuple[str, Any] | tuple[None, dict[str, Any]]:
    try:
        from gateway.session import SessionSource
        route = SessionSource.from_dict(dict(source))
        session_key = runner._session_key_for_source(route)
    except (KeyError, TypeError, ValueError) as exc:
        return None, _err("invalid_source", str(exc)[:256])
    if route.platform.value == "local":
        return None, _err("invalid_source", "background source routing is only available for messaging chats")
    return session_key, route


async def apost(ctx: Any, text: str, *, source: Mapping[str, Any] | None = None, editable: bool = True,
                delivery_id=None, checkpoint=None) -> PluginMessage:
    """``post`` for code already running on the gateway's event loop (async hooks/commands)."""
    if source is not None:
        runner, _loop = _gateway_loop()
        if runner is None:
            return PluginMessage(ctx.plugin_id, None, _err(
                "gateway_unavailable", "no live gateway is running in this process"))
        session_key, route = _directed_route(runner, source)
        if session_key is None:
            return PluginMessage(ctx.plugin_id, None, route)
        if not await asyncio.to_thread(ctx.gateway_injection_allowed):
            return PluginMessage(ctx.plugin_id, None, _err("permission_denied"))
        if not runner._plugin_injection_authorized(
                route, plugin_id=ctx.plugin_id, target=f"session={session_key}"):
            return PluginMessage(ctx.plugin_id, None, _err("permission_denied"))
        if delivery_id and not editable:
            from gateway.plugin_reports import with_transcript
            if not runner._plugin_messaging_session_owned(
                    session_key, plugin_home=ctx._manager.home_path, plugin_id=ctx.plugin_id):
                return PluginMessage(ctx.plugin_id, None, _err("permission_denied"))

            async def send():
                return (await _post_for(ctx, text, session_key, route, editable=False, directed=True)).result

            result = await with_transcript(ctx, runner, route, str(text), delivery_id, checkpoint, send,
                                           origin_key=session_key)
            return PluginMessage(ctx.plugin_id, None, result)
        return await _post_for(ctx, text, session_key, route, editable=editable)
    from gateway.plugin_messages import bound_route
    from gateway.session_context import bound_session_key
    return await _post_for(ctx, text, bound_session_key(), bound_route(), editable=editable)


def post(ctx: Any, text: str, *, source: Mapping[str, Any] | None = None) -> PluginMessage:
    """Post *text* to the calling session from a tool handler or hook on a worker thread. The session
    is read here, on the caller's own context, then the send runs on the gateway loop."""
    from gateway.session_context import bound_session_key
    session_key = bound_session_key() if source is None else "directed"
    if not session_key:
        return PluginMessage(ctx.plugin_id, None, _err("no_conversation", _NO_SESSION))
    directed_source = dict(source) if source is not None else None
    if directed_source is None:
        from gateway.plugin_messages import bound_route
        route = bound_route()
    else:
        route = None
    handle = PluginMessage(ctx.plugin_id, None, _err("timeout"))

    async def _post() -> dict[str, Any]:
        if directed_source is not None:
            handle._adopt(await apost(ctx, text, source=directed_source))
        else:
            handle._adopt(await _post_for(ctx, text, session_key, route))
        return handle.result

    result = _run_on_gateway_loop(_post(), handle)
    if handle._target is None and not handle.pending:
        handle.result = result
    return handle
