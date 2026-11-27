"""A ticket's queued notice must name a destination the outbox can actually reach.

A local write grant used to read as a destination whenever the profile owned a local
authority, while delivery for a local destination needs that authority's canonical Bot
Chat row. Without one the notice promised a report that could never land.
"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from gateway.config import GatewayConfig
from gateway.session import SessionStore
from persistence.core.identity import SessionId
from plugins.super_agent import _command, _has_destination, _post_gateway_admission
from plugins.super_agent.store import SuperAgentStore
from tests.gateway.test_follow_profile_followups import _create_bot_chat
from tests.gateway.test_session_follow_profile_config import _owner

HINT = "No writable notification destination"
REPORTS = "See ticket status for reports"
SETUP = "Select a write channel in Super Agent settings"


class _Runtime:
    """Just the surface ``_new_ticket`` uses: the store and its submit."""

    def __init__(self, store):
        self.store = store

    async def submit(self, **kwargs):
        return self.store.submit(**kwargs)


def _config(home: Path, workspace: Path, **settings) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(json.dumps({
        "model": {"provider": "custom", "default": "test", "base_url": "http://127.0.0.1:1/v1"},
        "plugins": {"enabled": ["super_agent"], "entries": {"super_agent": {
            "allow_gateway_injection": True,
            "settings": {"workspace_root": str(workspace), "message_sources": ["weixin"],
                         "write_message_sources": ["local"], **settings},
        }}},
    }), encoding="utf-8")


def _context(home: Path):
    from hermes_cli import plugins
    manifest = plugins.PluginManifest(name="super_agent", key="super_agent", source="bundled")
    return plugins.PluginContext(manifest, plugins.PluginManager(scope_key=str(home), owner_home=home))


def _served(tmp_path, monkeypatch, **settings):
    """A temp profile whose only grant is write-local, plus its live authority and plugin ctx."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    home = tmp_path / "home"
    _config(home, workspace, **settings)
    monkeypatch.setenv("HERMES_HOME", str(home))
    _store, runner, authority, owner = _owner(home)
    # No served home channel in this fixture; only the local destination is under test.
    runner._served_profile_for_home = lambda path: None
    runner._served_home_channel_configs = list
    ctx = _context(home)
    from hermes_cli import plugin_messages
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(plugin_messages, "_gateway_loop", lambda: (runner, loop))
    runtime = _Runtime(SuperAgentStore(tmp_path / "data", "default"))
    return SimpleNamespace(home=home, runner=runner, authority=authority, owner=owner, ctx=ctx,
                           runtime=runtime)


async def _submit(ctx, runtime, identity: str = "m1", text: str = "run tests"):
    result = await _post_gateway_admission(
        ctx, runtime, text="sa: " + text, message_id=identity,
        source={"platform": "weixin", "chat_id": "inbox", "user_id": "owner"})
    assert result["action"] == "handled", result
    return str(result["reply"])


@pytest.mark.asyncio
@pytest.mark.usefixtures("authority_cwd_in_tmp")
async def test_ticket_without_a_canonical_bot_chat_reports_the_setup_hint(tmp_path, monkeypatch):
    served = _served(tmp_path, monkeypatch)
    try:
        assert served.authority.persistence.sessions.get_by_title("Bot Chat") is None
        reply = await _submit(served.ctx, served.runtime)
        ticket, = served.runtime.store.list_tickets()
        assert ticket["id"] in reply
        assert HINT in reply and REPORTS in reply and SETUP in reply
        assert _has_destination(served.ctx, {"platform": "local", "bot_chat": True}) is False
    finally:
        await served.owner.close()


@pytest.mark.asyncio
@pytest.mark.usefixtures("authority_cwd_in_tmp")
async def test_a_canonical_bot_chat_in_this_profile_ends_the_hint(tmp_path, monkeypatch):
    served = _served(tmp_path, monkeypatch)
    try:
        from gateway import run
        monkeypatch.setattr(run, "_hermes_home", served.home)
        served.runner._peek_session_state = lambda key: None
        await _create_bot_chat(served.owner, served.authority, served.home, "bot",
                               title="Bot Chat", hidden=True)
        assert served.authority.persistence.sessions.get_by_title("Bot Chat") is not None

        reply = await _submit(served.ctx, served.runtime, identity="m2")
        ticket, = served.runtime.store.list_tickets()
        assert ticket["id"] in reply
        assert HINT not in reply
        assert _has_destination(served.ctx, {"platform": "local", "bot_chat": True}) is True
    finally:
        await served.owner.close()


@pytest.mark.asyncio
@pytest.mark.usefixtures("authority_cwd_in_tmp")
async def test_a_bot_chat_in_another_profile_is_not_this_profiles_destination(tmp_path, monkeypatch):
    served = _served(tmp_path, monkeypatch)
    other = served.home / "profiles" / "other"
    other.mkdir(parents=True)
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    token = set_hermes_home_override(str(other))
    try:
        other_store = SessionStore(other / "sessions", GatewayConfig())
        other_store.persistence.sessions.create_exclusive(
            SessionId("bot-chat-in-other"), source="local", title="Bot Chat")
        assert other_store.persistence.sessions.get_by_title("Bot Chat") is not None
    finally:
        reset_hermes_home_override(token)
    try:
        assert served.authority.persistence.sessions.get_by_title("Bot Chat") is None

        reply = await _submit(served.ctx, served.runtime)
        ticket, = served.runtime.store.list_tickets()
        assert ticket["id"] in reply and HINT in reply
        assert _has_destination(served.ctx, {"platform": "local", "bot_chat": True}) is False
    finally:
        other_store.close_all_db_handles()
        await served.owner.close()


@pytest.mark.asyncio
@pytest.mark.usefixtures("authority_cwd_in_tmp")
async def test_an_admitted_desktop_session_still_reports_back_without_a_bot_chat(tmp_path, monkeypatch):
    served = _served(tmp_path, monkeypatch, message_sources=["local"])
    try:
        assert served.authority.persistence.sessions.get_by_title("Bot Chat") is None
        assert _has_destination(served.ctx, {"platform": "local", "local_session_id": "sess-1",
                                             "actor_id": "human"}) is True
        reply = await _command(served.ctx, served.runtime, "new run tests",
                               source={"platform": "local", "chat_id": "sess-1", "user_id": "human"},
                               session_key="local:sess-1")
        ticket, = served.runtime.store.list_tickets()
        assert ticket["id"] in str(reply) and HINT not in str(reply)
        with served.runtime.store._connect() as conn:
            source_json, = conn.execute("SELECT source_json FROM triggers WHERE id=?",
                                        (ticket["trigger_id"],)).fetchone()
        assert json.loads(source_json)["local_session_id"] == "sess-1"
    finally:
        await served.owner.close()
