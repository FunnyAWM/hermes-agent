"""Durable reports across retries and local conversation admission."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from plugins.super_agent.store import SuperAgentStore


def test_discovery_failure_backoff_and_independent_destination_replay(tmp_path, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("plugins.super_agent.store._now", lambda: clock[0])
    store = SuperAgentStore(tmp_path, "alpha")
    ticket, _ = store.submit(task="test", sandbox="read-only", workspace_root=str(tmp_path),
                             source_instance="cli", external_event_id="event", source=None)
    assignment = store.claim_next()
    store.fail_assignment(assignment["id"], "original worker error", home_platforms=None)
    discovery = store.claim_outbox()
    store.settle_outbox(discovery["id"], delivered=False, error="home_discovery_unavailable")
    assert store.claim_outbox() is None
    clock[0] += 10
    discovery = store.claim_outbox()
    store.expand_outbox_homes(discovery, ["telegram", "discord"])
    telegram = store.claim_outbox()
    store.settle_outbox(telegram["id"], delivered=True, receipt="platform-receipt")
    discord = store.claim_outbox()
    store.settle_outbox(discord["id"], delivered=False, error="permission_denied")
    status = store.get_ticket(ticket["id"])
    assert status["last_error"] == "original worker error" and status["notice_denied"]
    assert {r["target"]: r["status"] for r in status["deliveries"]} == {"telegram": "delivered", "discord": "failed"}
    assert SuperAgentStore(tmp_path, "beta").redeliver(ticket["id"]) == 0
    assert store.redeliver(ticket["id"]) == 1
    replay = store.claim_outbox()
    assert (replay["id"], replay["attempts"]) == (discord["id"], 2)
    store.settle_outbox(replay["id"], delivered=True, attempt=1)
    assert store.get_ticket(ticket["id"])["deliveries"][0]["status"] == "sending"
    store.settle_outbox(replay["id"], delivered=True, attempt=2)
    assert store.claim_outbox() is None


def test_upgrade_leaves_historical_delivery_for_explicit_replay(tmp_path):
    store = SuperAgentStore(tmp_path, "alpha")
    ticket, _ = store.submit(task="legacy", sandbox="read-only", workspace_root=str(tmp_path),
                             source_instance="chat", external_event_id="old", source={"platform": "slack"})
    assignment = store.claim_next()
    assert store.complete_if_idle(assignment["id"], "outcome")
    sending = store.claim_outbox()
    with store._tx() as conn:
        conn.execute("DROP INDEX idx_outbox_due")
        for column in ("attempts", "next_attempt_at", "receipt"):
            conn.execute(f"ALTER TABLE outbox DROP COLUMN {column}")
    upgraded = SuperAgentStore(tmp_path, "alpha")
    upgraded.recover()
    assert upgraded.claim_outbox() is None
    historical, = upgraded.get_ticket(ticket["id"])["deliveries"]
    assert historical["status"] == "unknown"
    assert upgraded.redeliver(ticket["id"], historical["id"]) == 1
    assert upgraded.claim_outbox()["id"] == sending["id"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("authority_cwd_in_tmp")
async def test_local_report_replay_writes_one_assistant_message_in_its_own_conversation(tmp_path, monkeypatch):
    from hermes_cli import plugin_messages
    from hermes_cli.sync_sessions_bridge import _bot_messages
    from tests.gateway.test_session_follow_profile_config import _create, _owner

    home = tmp_path / "profile"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    store, runner, authority, owner = _owner(home)
    runner._session_key_for_source = lambda source: store._generate_session_key(source)
    runner._handle_message = AsyncMock(side_effect=AssertionError("a report must not execute a model turn"))
    monkeypatch.setattr(plugin_messages, "_gateway_loop", lambda: (runner, asyncio.get_running_loop()))
    ctx = SimpleNamespace(plugin_id="reports", _manager=SimpleNamespace(home_path=home), gateway_injection_allowed=lambda: True)
    try:
        live = await _create(owner, authority, home, "ordinary")
        sid, actor = live.source.chat_id, live.source.user_id
        for _ in range(2):
            ack = await plugin_messages.post_local_session(ctx, "result", sid, actor, "stable")
            assert ack.ok, ack
            if task := live.task:
                await asyncio.wait_for(asyncio.shield(task), 15)
        assert [(m["role"], m["content"]) for m in _bot_messages(authority.persistence, sid)] == [("assistant", "result")]
        assert (await plugin_messages.post_local_session(ctx, "bad", sid, "stranger", "second")).error == "permission_denied"
        assert (await plugin_messages.post_local_session(ctx, "bad", "missing", actor, "third")).error == "invalid_source"
    finally:
        await owner.close()
        store.close_all_db_handles()
