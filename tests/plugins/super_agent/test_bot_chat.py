"""A Desktop Bot Chat uses its own admitted text and retains the exchange for phone sync."""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from plugins.super_agent.store import SuperAgentStore
from tests.gateway.test_follow_profile_followups import _create_bot_chat
from tests.gateway.test_session_follow_profile_config import _owner


@pytest.mark.asyncio
@pytest.mark.usefixtures("authority_cwd_in_tmp")
async def test_bot_chat_submit_routes_before_inference_and_keeps_history(tmp_path, monkeypatch):
    from gateway import run
    from hermes_cli import plugins
    from hermes_cli.profiles import get_active_profile_name
    from hermes_cli.sync_sessions_bridge import _bot_messages
    from agent.plugin_llm import PluginLlm
    from types import SimpleNamespace
    classify = AsyncMock(return_value=SimpleNamespace(text="NEW"))
    monkeypatch.setattr(PluginLlm, "acomplete", classify)

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_BUNDLED_PLUGINS", str(Path(__file__).resolve().parents[3] / "plugins"))
    monkeypatch.setattr(run, "_hermes_home", home)
    (home / "config.yaml").write_text(
        'model: {provider: custom, default: test, base_url: "http://127.0.0.1:1/v1"}\n'
        'plugins:\n  enabled: [super_agent]\n  entries:\n    super_agent:\n'
        '      allow_gateway_injection: true\n'
        '      settings: {workspace_root: "' + home.as_posix() + '", message_sources: [local]}\n',
        encoding="utf-8",
    )
    plugins._reset_plugin_managers_for_tests()
    store, runner, authority, owner = _owner(home)
    runner._peek_session_state = lambda key: None
    runner._handle_message = AsyncMock(return_value="ordinary agent turn")

    async def send(sid, key, text):
        result = await owner.dispatch({"id": key, "method": "prompt.submit", "params": {
            "session_id": sid, "submission_id": key, "text": text}})
        assert "result" in result, result
        if task := authority.sessions[sid].task:
            await asyncio.wait_for(asyncio.shield(task), 15)

    try:
        live = await _create_bot_chat(owner, authority, home, "bot", title="Bot Chat", hidden=True)
        sid = live.source.chat_id
        plugins.discover_plugins()
        db, = (home / "plugin-data").glob("*/super_agent.db")
        tickets = SuperAgentStore(db.parent, get_active_profile_name())
        tickets.set_paused(True)
        await send(sid, "request", "Review the release")
        runner._handle_message.assert_not_awaited()
        await send(sid, "request", "Review the release")
        ticket, = tickets.list_tickets()
        assert ticket["task"] == "Review the release"
        followup = ticket["id"] + " include Windows"
        await send(sid, "followup", followup)
        history = [row["content"] for row in _bot_messages(authority.persistence, sid)]
        assert history == ["Review the release", followup, history[-1]] and "Follow-up" in history[-1]
        runner._handle_message.assert_not_awaited()
        classify.return_value = SimpleNamespace(text="CHAT")
        await send(sid, "semantic", "What is an installer?")
        runner._handle_message.assert_awaited_once()
        event, = runner._handle_message.await_args.args
        assert event._routed_by == ("super_agent",)

        from hermes_cli import plugin_messages
        ctx = plugins.PluginContext(plugins.PluginManifest(name="super_agent", key="super_agent", source="bundled"),
                                    plugins.get_plugin_manager())
        ctx.set_config("write_message_sources", [])
        await send(sid, "private", followup)
        await send(sid, "private-new", "sa: compare the packages")
        assert [row["content"] for row in _bot_messages(authority.persistence, sid)] == history
        assert runner._handle_message.await_count == 1

        ctx.set_config("message_sources", [])
        ctx.set_config("write_message_sources", ["local"])
        await send(sid, "disabled", "sa: should stay here")
        assert runner._handle_message.await_count == 2 and len(tickets.list_tickets()) == 2
        monkeypatch.setattr(plugin_messages, "_gateway_loop", lambda: (runner, asyncio.get_running_loop()))
        runner._session_key_for_source = lambda source: store._generate_session_key(source)
        receipt = await ctx.apost_home_channel("Build completed", platform="local")
        assert receipt.ok, receipt.result
        if task := authority.sessions[sid].task:
            await asyncio.wait_for(asyncio.shield(task), 15)
        notice = _bot_messages(authority.persistence, sid)[-1]
        assert (notice["role"], notice["content"]) == ("assistant", "Build completed")
        assert runner._handle_message.await_count == 2
    finally:
        await owner.close()
        store.close_all_db_handles()
        plugins.get_plugin_manager().unload()
        plugins._reset_plugin_managers_for_tests()
