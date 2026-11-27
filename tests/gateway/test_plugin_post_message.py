"""``PluginContext.post_message``: one message in the calling session's chat, edited in place."""

import asyncio
import contextvars
import json
from unittest.mock import MagicMock, patch

import pytest

from gateway.config import GatewayConfig, Platform
from gateway.platforms.base import BasePlatformAdapter, PlatformConfig, SendResult
from gateway.run import GatewayRunner
from gateway.session import SessionSource, SessionStore
from gateway.session_context import scoped_session_key
from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest


class _Adapter(BasePlatformAdapter):
    def __init__(self):
        super().__init__(PlatformConfig(enabled=True, token="t"), Platform.TELEGRAM)
        self.calls = []

    async def connect(self, *, is_reconnect=False):
        return True

    async def disconnect(self):
        return None

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        assert (metadata or {}).get("_interim_send") is True  # never sealed as the turn's answer
        self.calls.append(("send", chat_id, content))
        return SendResult(success=True, message_id=f"m{len(self.calls)}")

    async def get_chat_info(self, chat_id):
        return {"id": chat_id, "type": "dm"}


class _EditingAdapter(_Adapter):
    async def edit_message(self, chat_id, message_id, content, *, finalize=False):
        self.calls.append(("edit", chat_id, message_id, content))
        return SendResult(success=True, message_id=message_id)


class _RaisingEditAdapter(_Adapter):
    async def edit_message(self, chat_id, message_id, content, *, finalize=False):
        raise ValueError("malformed edit response")


class _FailedEditAdapter(_Adapter):
    async def edit_message(self, chat_id, message_id, content, *, finalize=False):
        return SendResult(success=False)  # no error text, as an empty platform response gives


def _setup(tmp_path, adapter, *, source=None):
    store = SessionStore(sessions_dir=tmp_path / "sessions", config=GatewayConfig())
    entry = store.get_or_create_session(source or SessionSource(
        platform=Platform.TELEGRAM, chat_id="42", chat_type="dm", user_id="42", user_name="u"))
    runner = object.__new__(GatewayRunner)
    runner.session_store = store
    runner.adapters = {entry.origin.platform: adapter}
    runner._profile_adapters = {}
    runner._gateway_loop = asyncio.get_running_loop()
    runner._running = True
    runner._is_user_authorized = MagicMock(return_value=True)
    ctx = PluginContext(PluginManifest(name="progress", key="progress", source="user"), PluginManager())
    return runner, ctx, entry


async def _in_session(session_key, fn):
    """Run *fn* on a worker thread with the session bound, as a tool handler is."""
    def bound():
        with scoped_session_key(session_key):
            return fn()
    return await asyncio.to_thread(contextvars.copy_context().run, bound)


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_cls, edited",
                         [(_EditingAdapter, True), (_Adapter, False), (_RaisingEditAdapter, False),
                          (_FailedEditAdapter, False)])
async def test_post_then_edit_targets_the_calling_session(tmp_path, adapter_cls, edited):
    adapter = adapter_cls()
    runner, ctx, entry = _setup(tmp_path, adapter)
    with patch("gateway.run._gateway_runner_ref", lambda: runner), \
            patch.object(GatewayRunner, "_plugin_messaging_session_owned", return_value=True):
        handle = await _in_session(entry.session_key, lambda: ctx.post_message("step 1/2"))
        assert handle.ok and handle.message_id == "m1"
        result = await _in_session("", lambda: handle.edit("step 2/2"))  # the handle carries its route
    assert result["ok"] is True and result["edited"] is edited
    if edited:
        assert adapter.calls == [("send", "42", "step 1/2"), ("edit", "42", "m1", "step 2/2")]
    else:  # no edit support, a raised or a failed edit: a fresh message, and the handle follows it
        assert adapter.calls == [("send", "42", "step 1/2"), ("send", "42", "step 2/2")]
        assert result["fallback"] is True and handle.message_id == "m2"


@pytest.mark.asyncio
async def test_chat_tool_notice_goes_to_adapter_without_echoing_into_model(tmp_path):
    from gateway.platforms.event import MessageEvent
    from gateway.session_context import clear_session_vars, set_session_vars
    from hermes_cli.plugin_command_dispatch import PluginNotice

    adapter = _Adapter()
    runner, ctx, entry = _setup(tmp_path, adapter)
    notice = PluginNotice("Queued as private-ticket-123", "task.confirmed")

    async def accept(_chat):
        return notice

    event = MessageEvent(text="Compare the packages", source=entry.origin, message_id="user-id")
    tokens = set_session_vars(session_key=entry.session_key, turn_event=event)
    try:
        with (patch("gateway.run._gateway_runner_ref", lambda: runner),
              patch.object(GatewayRunner, "_plugin_messaging_session_owned", return_value=True) as owned):
            shown = await ctx.in_gateway_chat(accept)
            owned.return_value = False
            refused = await ctx.in_gateway_chat(accept)
        assert notice not in shown and "received" in shown
        assert notice not in refused and "could not be delivered" in refused
        assert adapter.calls == [("send", "42", str(notice))]
    finally:
        clear_session_vars(tokens)
        runner.session_store.close_all_db_handles()


@pytest.mark.asyncio
async def test_no_bound_session_is_refused_without_sending(tmp_path):
    adapter = _EditingAdapter()
    runner, ctx, _entry = _setup(tmp_path, adapter)
    with patch("gateway.run._gateway_runner_ref", lambda: runner):
        handle = await asyncio.to_thread(ctx.post_message, "from a timer")
        edit = await asyncio.to_thread(handle.edit, "still nothing")
    assert (handle.ok, handle.error, edit["ok"]) == (False, "no_conversation", False)
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_background_post_uses_the_admitted_source_snapshot(tmp_path):
    adapter = _EditingAdapter()
    adapter.MAX_MESSAGE_LENGTH = 64
    runner, ctx, entry = _setup(tmp_path, adapter)
    source = entry.origin.to_dict()
    runner.session_store.remove_by_session_id(entry.session_id)
    with patch("gateway.run._gateway_runner_ref", lambda: runner), \
            patch.object(ctx, "gateway_injection_allowed", return_value=True), \
            patch.object(GatewayRunner, "_plugin_messaging_session_owned", return_value=True), \
            patch.object(GatewayRunner, "_plugin_injection_authorized", return_value=True):
        report = ("x" * 125) + "TAIL"
        handle = await ctx.apost_message(report, source=source)
        assert handle.ok and len(adapter.calls) == 1 and len(adapter.calls[0][2]) <= 64
        adapter.calls.clear()
        receipt = await ctx.apost_message(report, source=source, editable=False)
        assert receipt.ok and (await receipt.aedit("replace"))["error"] == "not_editable"
    sent = [text for kind, chat_id, text in adapter.calls if kind == "send" and chat_id == "42"]
    assert len(sent) > 1 and all(len(part) <= 64 for part in sent)
    assert sum(part.count("x") for part in sent) == 125 and "TAIL" in sent[-1]


@pytest.mark.asyncio
async def test_background_post_refuses_invalid_or_revoked_source_without_fallback(tmp_path):
    adapter = _EditingAdapter()
    runner, ctx, entry = _setup(tmp_path, adapter)
    with patch("gateway.run._gateway_runner_ref", lambda: runner):
        invalid = await ctx.apost_message("must not send", source={})
    with patch("gateway.run._gateway_runner_ref", lambda: runner), \
            patch.object(ctx, "gateway_injection_allowed", return_value=True) as grant, \
            patch.object(GatewayRunner, "_plugin_injection_authorized", return_value=False) as allowed:
        revoked = await ctx.apost_message("must not send", source=entry.origin.to_dict())
        allowed.return_value = True
        grant.return_value = False
        ungranted = await ctx.apost_message("must not send", source=entry.origin.to_dict(), editable=False)

    assert (invalid.ok, invalid.error) == (False, "invalid_source")
    assert (revoked.ok, revoked.error) == (False, "permission_denied")
    assert (ungranted.ok, ungranted.error) == (False, "permission_denied")
    assert adapter.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("partial", [False, True])
async def test_home_post_uses_only_granted_profile_home_and_topic(tmp_path, partial):
    from unittest.mock import AsyncMock
    from gateway.config import HomeChannel

    adapter = _EditingAdapter()
    runner, ctx, _entry = _setup(tmp_path, adapter)
    runner._primary_profile_name = "default"
    platform = Platform.TELEGRAM
    primary = HomeChannel(platform=platform, chat_id="primary", name="Home")
    secondary = HomeChannel(platform=platform, chat_id="project", name="Project", thread_id="topic")
    runner.config = GatewayConfig(platforms={platform: PlatformConfig(enabled=True, home_channel=primary)})
    runner._profile_configs = {"beta": GatewayConfig(platforms={
        platform: PlatformConfig(enabled=True, home_channel=secondary)})}
    runner._profile_adapters = {"beta": {platform: adapter}}
    result = SendResult(success=True, message_id="sent")
    if partial:
        adapter._resume_partial_send = AsyncMock(return_value=result)
        result = SendResult(success=False, error="rate limited", raw_response={
            "partial_overflow": True, "delivered_chunks": 1, "undelivered_chunks": ["last part"],
        })
    adapter.send = AsyncMock(return_value=result)
    with patch("gateway.run._gateway_runner_ref", lambda: runner), \
            patch.object(runner, "_served_profile_for_home", return_value="beta"), \
            patch.object(ctx, "gateway_injection_allowed", return_value=False) as grant:
        assert (await ctx.apost_home_channel("private", platform="telegram")).error == "permission_denied"
        grant.return_value = True
        assert "telegram" in ctx.home_channel_platforms()
        receipt = await ctx.apost_home_channel("done", platform="telegram")
        assert receipt.ok and receipt.message_id == "sent", receipt.result
        assert (await ctx.apost_home_channel("private", platform="slack")).error == "home_channel_unavailable"
    adapter.send.assert_awaited_once()
    assert adapter.send.await_args.args[:2] == ("project", "done")
    assert adapter.send.await_args.kwargs["metadata"]["thread_id"] == "topic"
    if partial:
        adapter._resume_partial_send.assert_awaited_once()
        assert adapter._resume_partial_send.await_args.args == ("project", result)


@pytest.mark.asyncio
@pytest.mark.parametrize("home_channel", [False, True])
async def test_confirmed_external_report_replays_once_in_display_and_next_turn(tmp_path, home_channel):
    from unittest.mock import AsyncMock
    from gateway.config import HomeChannel
    from persistence.conversation.messages import NewMessage, TranscriptRead, TranscriptView

    adapter = _EditingAdapter()
    adapter.MAX_MESSAGE_LENGTH = 64
    adapter.send = AsyncMock(return_value=SendResult(success=True, message_id="receipt"))
    platform = Platform.TELEGRAM if home_channel else Platform.DISCORD
    source = SessionSource(platform=platform, chat_id="42", chat_type="dm", user_id="42",
                           scope_id=None if home_channel else "legacy-guild")
    runner, ctx, entry = _setup(tmp_path, adapter, source=source)
    home = HomeChannel(platform=platform, chat_id="42", name="Home")
    runner.config = GatewayConfig(platforms={platform: PlatformConfig(enabled=True, home_channel=home)})
    runner._primary_profile_name = "default"
    store = runner.session_store._persistence_for_key(entry.session_key)
    sid = entry.session_id
    if not home_channel:
        legacy = source.to_dict()
        del legacy["scope_id"]  # pre-migration Discord transcripts only stored guild_id
        store.routing.record_session_peer(sid, source=platform.value, user_id="42", session_key=entry.session_key,
                                          chat_id="42", chat_type="dm", origin_json=json.dumps(legacy))
    report = "worker result " * 30 + "END"
    store.messages.append(sid, NewMessage(role="user", content="what happened?"))

    async def deliver(**kwargs):
        if home_channel:
            return await ctx.apost_home_channel(report, platform="telegram", delivery_id="turn-7", **kwargs)
        return await ctx.apost_message(report, source=entry.origin.to_dict(), editable=False, delivery_id="turn-7", **kwargs)

    try:
        with (patch("gateway.run._gateway_runner_ref", lambda: runner),
              patch.object(ctx, "gateway_injection_allowed", return_value=True),
              patch.object(runner, "_served_profile_for_home", return_value="default"),
              patch.object(runner, "_plugin_messaging_session_owned", return_value=True),
              patch.object(runner, "_plugin_injection_authorized", return_value=True)):
            assert store.coordination.try_acquire_turn_lease(session_id=sid, holder="other-turn")
            try:
                pending = await deliver()
                assert pending.error == "history_pending", pending.result
            finally:
                store.coordination.release_turn_lease(session_id=sid, holder="other-turn")
            count = adapter.send.await_count
            assert count > 1
            recovered = await deliver(checkpoint=pending.result["delivery_checkpoint"])
            assert recovered.ok and recovered.message_id == "receipt"
            assert adapter.send.await_count == count
            newer = runner.session_store.reset_session(entry.session_key)
            assert (await deliver()).ok  # sender lost the last acknowledgement: replay the logical id
        assert [(m.role, m.content) for m in store.messages.read_transcript(sid, TranscriptRead(view=TranscriptView.DISPLAY))
                if m.role == "assistant"] == [("assistant", report)]
        assert [m["content"] for m in runner.session_store.load_transcript(sid) if m["role"] == "assistant"] == [report]
        assert not runner.session_store.load_transcript(newer.session_id)
    finally:
        runner.session_store.close_all_db_handles()
