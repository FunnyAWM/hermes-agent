"""Super Agent command, admission, and real discovery behavior."""

import asyncio
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

import hermes_yaml as yaml

from plugins.super_agent import _command, _post_gateway_admission, register
from plugins.super_agent.runtime import SuperAgentRuntime
from plugins.super_agent.store import SuperAgentStore


class _Context:
    plugin_id = "super_agent"
    profile_name = "default"

    def __init__(self, data_dir, workspace):
        self.state = SimpleNamespace(data_dir=data_dir)
        self.config = {"workspace_root": str(workspace), "default_sandbox": "workspace-write"}
        self.hooks = {}
        self.commands = {}
        self.unload = []

    def get_config(self, name, default=None):
        return self.config.get(name, default)

    def gateway_injection_allowed(self):
        return True

    def home_channel_platforms(self, **kwargs):
        return []

    def register_hook(self, name, callback):
        self.hooks[name] = callback

    def register_command(self, name, callback, **_kwargs):
        self.commands[name] = callback

    def on_unload(self, callback):
        self.unload.append(callback)

    def spawn_task(self, coroutine, **_kwargs):
        coroutine.close()
        raise AssertionError("worker-mode registration must not start the Router")


class _Runtime:
    watchdog_seconds = 60

    def __init__(self, store):
        self.store = store

    async def submit(self, **kwargs):
        return self.store.submit(**kwargs)

    async def tell(self, ticket_id, text, **kwargs):
        return self.store.add_inbox(ticket_id, text, **kwargs)

    async def cancel(self, ticket_id, **kwargs):
        return self.store.cancel(ticket_id, **kwargs)

    async def takeover(self, ticket_id):
        return self.store.takeover(ticket_id)

    async def retry(self, ticket_id):
        return self.store.retry(ticket_id)

    async def pause(self):
        return self.store.set_paused(True)

    async def resume(self):
        return self.store.set_paused(False)


@pytest.fixture
def rig(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ctx = _Context(tmp_path / "data", workspace)
    return ctx, _Runtime(SuperAgentStore(ctx.state.data_dir, ctx.profile_name))


@pytest.mark.asyncio
async def test_admission_is_selective_idempotent_and_preserves_source(rig):
    ctx, runtime = rig
    source = {"platform": "telegram", "chat_id": "群聊-42", "thread_id": "主题-7"}

    first = await _post_gateway_admission(
        ctx, runtime, text="  SA : 修复登录问题  ", source=source,
        message_id="message-1", session_key="telegram:42",
    )
    replay = await _post_gateway_admission(
        ctx, runtime, text="  SA : 修复登录问题  ", source=source,
        message_id="message-1", session_key="telegram:42",
    )

    assert first["action"] == replay["action"] == "handled"
    assert "queued" in first["reply"]
    assert "already queued" in replay["reply"]
    [ticket] = runtime.store.list_tickets()
    assert runtime.store.counts() == {"queued": 1}
    assert ticket["task"] == "修复登录问题"

    ordinary = await _post_gateway_admission(
        ctx, runtime, text="ordinary message", source=source, message_id="message-2"
    )
    assert ordinary["action"] == "continue"
    rejected = await _post_gateway_admission(
        ctx, runtime, text="sa:", source=source, message_id="message-3"
    )
    assert rejected["action"] == "handled" and "rejected" in rejected["reply"]
    assert runtime.store.counts() == {"queued": 1}


@pytest.mark.asyncio
async def test_gateway_subscription_routes_followups_and_honours_observe_and_unsubscribe(rig, monkeypatch):
    ctx, fixture = rig
    runtime = SuperAgentRuntime(ctx, fixture.store)
    monkeypatch.setattr(runtime, "wake", lambda: None)
    source = {"platform": "weixin", "chat_id": "room", "thread_id": "topic"}
    channel = {"source": source, "session_key": "weixin-session"}
    thread_key = "weixin:room:topic"

    await _post_gateway_admission(ctx, runtime, text="sa: count items", message_id="1", **channel)
    assignment = runtime.store.claim_next()
    ticket_id = assignment["ticket_id"]
    await _command(ctx, runtime, f"subscribe {ticket_id} {thread_key}", **channel)
    await _command(ctx, runtime, f"observe {thread_key} observe", **channel)

    assert (await _post_gateway_admission(ctx, runtime, text="preview", message_id="2", **channel))["action"] == "continue"
    assert [row["detail"] for row in runtime.store.list_observe_log()] == ["preview"]
    assert runtime.store.create_followup_turn(assignment["id"]) is None

    await _command(ctx, runtime, f"observe {thread_key} auto", **channel)
    other = await _post_gateway_admission(
        ctx, runtime, text="other conversation", message_id="other-3", source=source, session_key="other-session"
    )
    assert other["action"] == "continue"
    assert runtime.store.create_followup_turn(assignment["id"]) is None
    forwarded = await _post_gateway_admission(ctx, runtime, text="count blue items", message_id="3", **channel)
    assert forwarded and forwarded["action"] == "handled"
    turn = runtime.store.create_followup_turn(assignment["id"])
    assert "count blue items" in turn["text"]
    runtime.store.mark_turn_terminal(turn["id"], "counted")
    await _command(ctx, runtime, f"unsubscribe {ticket_id} {thread_key}", **channel)
    independent = await _post_gateway_admission(ctx, runtime, text="ordinary", message_id="4", **channel)
    assert independent["action"] == "continue"
    assert runtime.store.create_followup_turn(assignment["id"]) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sources", "expected"),
    [(["*"], "queued"), (["telegram", " WEIXIN "], "queued"), ([], None),
     (["unknown"], None), ("weixin", "rejected"), (["weixin", 1], "rejected")],
)
async def test_message_source_changes_apply_to_the_next_admission(rig, sources, expected):
    ctx, runtime = rig
    source = {"platform": "weixin", "chat_id": "room"}
    await _post_gateway_admission(ctx, runtime, text="sa: original", message_id="1", source=source)
    ctx.config["message_sources"] = sources
    ordinary = await _post_gateway_admission(ctx, runtime, text="hello", message_id="2", source=source)
    assert ordinary is None or ordinary["action"] == "continue"
    reply = await _post_gateway_admission(ctx, runtime, text="sa: next", message_id="3", source=source)
    if expected is None:
        assert reply is None
    else:
        assert reply["action"] == "handled" and expected in reply["reply"]
    tasks = {row["task"] for row in runtime.store.list_tickets()}
    assert tasks == ({"original", "next"} if expected == "queued" else {"original"})


@pytest.mark.asyncio
async def test_explicit_tasks_without_message_ids_are_distinct_and_controls_stay_local(rig):
    ctx, runtime = rig
    source = {"platform": "discord", "chat_id": "room"}
    for _ in range(2):
        result = await _post_gateway_admission(ctx, runtime, text="sa: run again", source=source)
        assert result["action"] == "handled"
    for text, kind in [("/help", "command"), ("photo caption", "photo"), ("  ", "text")]:
        assert await _post_gateway_admission(ctx, runtime, text=text, source=source, message_type=kind) is None
    assert runtime.store.counts() == {"queued": 2}


@pytest.mark.asyncio
async def test_commands_update_store_and_reject_unsafe_new_requests(rig, tmp_path):
    ctx, runtime = rig

    missing = await _command(ctx, runtime, "new")
    unsafe = await _command(ctx, runtime, "new full-access delete everything")
    assert "task is required" in missing.lower()
    assert "full-access" in unsafe.lower() and "rejected" in unsafe.lower()
    assert runtime.store.list_tickets() == []

    queued = await _command(ctx, runtime, "new read-only inspect the repository")
    ticket_id = queued.split()[2]
    assert runtime.store.get_ticket(ticket_id)["sandbox"] == "read-only"
    assert ticket_id in await _command(ctx, runtime, "status " + ticket_id)
    assert ticket_id in await _command(ctx, runtime, "list")
    assert "Follow-up queued" in await _command(ctx, runtime, f"tell {ticket_id} add tests")

    assert "cancelled" in await _command(ctx, runtime, "cancel " + ticket_id)
    assert runtime.store.get_ticket(ticket_id)["status"] == "cancelled"
    assert "queued for retry" in await _command(ctx, runtime, "retry " + ticket_id)
    assert runtime.store.get_ticket(ticket_id)["status"] == "queued"

    other = SuperAgentStore(tmp_path / "other", "other")
    foreign, _ = other.submit(
        task="secret", sandbox="read-only", workspace_root="workspace",
        source_instance="test", external_event_id="foreign", source=None,
    )
    assert await _command(ctx, runtime, "status " + foreign["id"]) == (
        f"Unknown Super Agent ticket: {foreign['id']}"
    )


@pytest.mark.asyncio
async def test_operator_commands_report_router_state_and_control_audit(rig):
    ctx, runtime = rig
    ticket, _ = runtime.store.submit(
        task="inspect the repository",
        sandbox="workspace-write",
        workspace_root="workspace",
        source_instance="telegram:bot-1",
        external_event_id="operator-controls",
        source={"chat_id": "42", "token": "must-not-leak"},
    )
    assignment = runtime.store.claim_next()
    turn = runtime.store.ensure_initial_turn(assignment["id"], ticket["task"])
    runtime.store.mark_turn_submitted(turn["id"], "admission-1")
    runtime.store.bind_worker_session(assignment["id"], "worker-1")

    assert await _command(ctx, runtime, "pause") == "Super Agent Router paused."
    assert await _command(ctx, runtime, "pause") == "Super Agent Router is already paused."
    health = await _command(ctx, runtime, "health")
    listing = await _command(ctx, runtime, "list")
    status = await _command(ctx, runtime, f"status {ticket['id']}")
    assert "Router: paused" in health and "pause router" in health
    assert "Router: paused; Watchdog alerts: 0" in listing
    assert "Worker Session: worker-1 (read-only)" in status
    assert "Retry budget: 0/3" in status
    assert "must-not-leak" not in health + listing + status

    assert await _command(ctx, runtime, "resume") == "Super Agent Router resumed."
    assert await _command(ctx, runtime, "resume") == "Super Agent Router is already running."
    assert "taken over" in await _command(ctx, runtime, f"takeover {ticket['id']}")
    taken_over = await _command(ctx, runtime, f"status {ticket['id']}")
    assert "taken_over" in taken_over and "Recovery record: takeover" in taken_over

    assert "queued for retry" in await _command(ctx, runtime, f"retry {ticket['id']}")
    assert runtime.store.claim_next() is None
    stop, = runtime.store.pending_stops()
    assert (stop["id"], stop["admission_id"]) == (turn["id"], "admission-1")
    runtime.store.confirm_stopped(stop["id"])
    current = runtime.store.claim_next()
    current_turn = runtime.store.ensure_initial_turn(current["id"], ticket["task"])
    runtime.store.mark_turn_submitted(current_turn["id"], "admission-2")
    runtime.store.bind_worker_session(current["id"], "worker-2")
    stopped = await _command(ctx, runtime, f"stop {ticket['id']}")
    assert "cancelled" in stopped
    assert await _command(ctx, runtime, f"stop {ticket['id']}") == stopped
    assert [
        event["action"] for event in runtime.store.list_control_events(ticket["id"], limit=20)
    ].count("cancel") == 1
    final = await _command(ctx, runtime, f"status {ticket['id']}")
    final_health = await _command(ctx, runtime, "health")
    assert "cancelled" in final and "Retry budget: 1/3" in final
    for action in ("pause", "resume", "takeover", "retry", "cancel"):
        assert action in final_health


@pytest.mark.asyncio
async def test_health_and_status_show_only_active_watchdog_alerts(rig):
    ctx, runtime = rig
    ticket, _ = runtime.store.submit(
        task="long-running task",
        sandbox="workspace-write",
        workspace_root="workspace",
        source_instance="test",
        external_event_id="watchdog",
        source={"chat_id": "42"},
    )
    assignment = runtime.store.claim_next()
    with runtime.store._connect() as conn:
        conn.execute("UPDATE assignments SET updated_at=0 WHERE id=?", (assignment["id"],))
        conn.commit()
    assert len(runtime.store.scan_watchdog(1)) == 1

    health = await _command(ctx, runtime, "health")
    status = await _command(ctx, runtime, f"status {ticket['id']}")
    assert "Watchdog: 1 active" in health
    assert "Alert " + ticket["id"] in health
    assert "Watchdog: No persisted Assignment or Turn state change" in status
    assert "Suggested action:" in status

    assert runtime.store.takeover(ticket["id"]) is not None
    assert runtime.store.scan_watchdog(1) == []
    assert "Watchdog: 0 active" in await _command(ctx, runtime, "health")
    assert "Watchdog:" not in await _command(ctx, runtime, f"status {ticket['id']}")


def test_worker_process_registers_only_the_sandbox_hook(tmp_path, monkeypatch):
    ctx = _Context(tmp_path / "data", tmp_path)
    monkeypatch.setattr("plugins.super_agent.is_worker_process", lambda: True)

    register(ctx)

    assert set(ctx.hooks) == {"pre_tool_call"}
    assert ctx.commands == {}
    assert ctx.unload == []


@pytest.fixture
def discovered_super_agent(tmp_path, monkeypatch):
    from hermes_cli import plugins

    home = tmp_path / "hermes-home"
    plugin_dir = home / "plugins" / "super_agent"
    workspace = tmp_path / "workspace"
    empty_bundled = tmp_path / "empty-bundled"
    home.mkdir()
    workspace.mkdir()
    empty_bundled.mkdir()
    shutil.copytree(
        Path(__file__).resolve().parents[3] / "plugins" / "super_agent",
        plugin_dir,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    (home / "config.yaml").write_text(yaml.safe_dump({
        "plugins": {
            "enabled": ["super_agent"],
            "entries": {"super_agent": {"settings": {"workspace_root": str(workspace)}}},
        }
    }), encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_BUNDLED_PLUGINS", str(empty_bundled))
    plugins._reset_plugin_managers_for_tests()
    plugins.discover_plugins()
    manager = plugins.get_plugin_manager()
    yield plugins, manager, home
    manager.unload()
    plugins._reset_plugin_managers_for_tests()


@pytest.mark.asyncio
async def test_chinese_commands_and_admission(rig, monkeypatch):
    from agent.i18n import reset_language_cache
    from unittest.mock import AsyncMock

    monkeypatch.setenv("HERMES_LANGUAGE", "zh")
    reset_language_cache()
    ctx, runtime = rig
    ctx.llm = SimpleNamespace(acomplete=AsyncMock(return_value=SimpleNamespace(text="ASK")))
    assert "Super Agent 命令" in await _command(ctx, runtime, "help")
    reply = await _post_gateway_admission(ctx, runtime, text="运行测试", source={"platform": "weixin", "chat_id": "1"})
    assert "转交" in reply["reply"] and runtime.store.list_tickets() == []
    reply = await _post_gateway_admission(ctx, runtime, text="确认转交", source={"platform": "weixin", "chat_id": "1"})
    assert "已入队" in reply["reply"]
    ticket, = runtime.store.list_tickets()
    assert "任务：运行测试" in await _command(ctx, runtime, f"status {ticket['id']}")


@pytest.mark.asyncio
async def test_discovered_router_consumes_whatsapp_text_before_channel_agent(discovered_super_agent, monkeypatch):
    from unittest.mock import AsyncMock
    from agent.plugin_llm import PluginLlm
    monkeypatch.setattr(PluginLlm, "acomplete", AsyncMock(return_value=SimpleNamespace(text="NEW")))
    from hermes_cli.profiles import get_active_profile_name
    from tests.gateway.test_post_gateway_admission import _event, _runner

    _, manager, home = discovered_super_agent
    path, = (home / "plugin-data").glob("*/super_agent.db")
    store = SuperAgentStore(path.parent, get_active_profile_name())
    store.set_paused(True)
    runner = _runner()
    try:
        reply = await runner._handle_message(_event("check the release"))
        assert "queued" in reply
        ticket, = store.list_tickets()
        assert ticket["task"] == "check the release"
        runner._handle_message_with_agent.assert_not_awaited()
    finally:
        manager.unload("super_agent")


def test_real_discovery_registers_and_unload_releases_surfaces(discovered_super_agent):
    from hermes_cli.tools_config import _get_platform_tools
    from tools.registry import registry
    plugins, manager, home = discovered_super_agent
    [row] = [plugin for plugin in manager.list_plugins() if plugin["key"] == "super_agent"]
    assert row["enabled"] is True and not row.get("error"), row
    assert plugins.get_plugin_command_handler("super-agent") is not None
    assert manager.has_hook("post_gateway_admission")
    for platform in ("cli", "weixin", "telegram"):
        assert "super_agent" in _get_platform_tools({}, platform)
    assert manager.render_system_prompt_sections({"platform": "local"})
    assert len(list((home / "plugin-data").glob("*/super_agent.db"))) == 1

    assert manager.unload("super_agent") is True
    assert plugins.get_plugin_command_handler("super-agent") is None
    assert not manager.has_hook("post_gateway_admission")
    assert registry.get_entry("super_agent_route", scope=manager.scope_key) is None
    assert not manager.render_system_prompt_sections({"platform": "local"})


@pytest.mark.asyncio
async def test_local_model_tool_offers_original_input_and_routes_using_briefs(discovered_super_agent, monkeypatch):
    from gateway.config import Platform
    from gateway.platforms.event import MessageEvent
    from gateway.session import SessionSource
    from gateway.session_context import clear_session_vars, set_session_vars
    from hermes_cli import plugin_messages
    from hermes_cli.profiles import get_active_profile_name
    from tools.registry import registry

    _, manager, home = discovered_super_agent
    db, = (home / "plugin-data").glob("*/super_agent.db")
    store = SuperAgentStore(db.parent, get_active_profile_name())
    store.set_paused(True)
    loop = asyncio.get_running_loop()
    from unittest.mock import Mock
    live = SimpleNamespace(event_stream=Mock())
    runner = SimpleNamespace(_plugin_local_session=lambda *a, **kw: live)
    monkeypatch.setattr(plugin_messages, "_gateway_loop", lambda: (runner, loop))
    source = SessionSource(platform=Platform.LOCAL, chat_id="desktop", user_id="owner")
    route = "agent:default:local:desktop"

    async def call(event, action, **kwargs):
        tokens = set_session_vars(session_key=route, turn_event=event)
        try:
            return await asyncio.to_thread(registry.dispatch, "super_agent_route",
                                           {"action": action, **kwargs}, scope=manager.scope_key)
        finally:
            clear_session_vars(tokens)

    request = MessageEvent(text="Compare login times", source=source, message_id="1")
    assert json.loads(await call(request, "inspect")) == []
    assert "yes or no" in await call(request, "offer")
    assert store.list_tickets() == []
    yes = MessageEvent(text="yes", source=source, message_id="2")
    assert json.loads(await call(yes, "inspect")) == []  # discovery never consumes consent
    assert "accepted" in await call(yes, "answer")
    assert live.event_stream.publish.call_args.kwargs["event_type"] == "notification.show"
    assert "no pending" in await call(yes, "answer")
    ticket, = store.list_tickets()
    assert ticket["task"] == request.text
    await _command(_Context(db.parent, home), _Runtime(store), "new Review release notes", session_key=route)
    followup = MessageEvent(text="Include token refresh latency", source=source, message_id="3")
    before = json.loads(await call(followup, "inspect"))
    target, = [brief for brief in before if brief["goal"] == request.text]
    assert len(before) == 2 and target["id"] == ticket["id"]
    for _ in range(2):
        assert ticket["id"] in await call(followup, "forward", ticket_id=ticket["id"])
    briefs = json.loads(await call(followup, "inspect"))
    assert {b["goal"]: b["latest_followup"] for b in briefs} == {
        request.text: followup.text, "Review release notes": None,
    }
    route = "agent:default:local:other"
    assert json.loads(await call(followup, "inspect")) == []
    assert "Unknown" in await call(followup, "forward", ticket_id=ticket["id"])
    assert "live gateway chat turn" in await call(
        MessageEvent(text="yes", source=source, internal=True), "answer",
    )


@pytest.mark.asyncio
async def test_shared_chat_consent_and_explicit_followup_belong_to_sender(rig):
    from unittest.mock import AsyncMock
    ctx, runtime = rig
    ctx.llm = SimpleNamespace(acomplete=AsyncMock(return_value=SimpleNamespace(text="ASK")))
    source = {"platform": "telegram", "chat_id": "group", "chat_type": "group"}

    async def send(actor, text, message_id):
        return await _post_gateway_admission(ctx, runtime, source=source, actor_id=actor, text=text,
                                             message_id=message_id, session_key="shared")

    assert "yes or no" in (await send("alice", "Fix login", "m1"))["reply"]
    assert await send("bob", "yes", "m2") is None
    assert await send("", "yes", "m3") is None
    assert (await send("alice", "no", "m4"))["text"] == "Fix login"
    assert runtime.store.list_tickets() == []
    await send("alice", "Fix logout", "m5")
    await send("alice", "yes", "m6")
    ticket, = runtime.store.list_tickets()
    followup = ticket["id"] + " include Firefox"
    assert "Unknown" in (await send("bob", followup, "m7"))["reply"]
    for _ in range(2):
        assert "Follow-up queued" in (await send("alice", followup, "m8"))["reply"]
    assignment = runtime.store.claim_next()
    assert runtime.store.create_followup_turn(assignment["id"])["text"].count(followup) == 1


@pytest.mark.asyncio
async def test_declined_handoff_runs_the_original_gateway_turn(rig):
    from functools import partial
    from unittest.mock import AsyncMock
    from tests.gateway.test_post_gateway_admission import _event, _register, _runner

    ctx, runtime = rig
    ctx.config["write_message_sources"] = []
    ctx.llm = SimpleNamespace(acomplete=AsyncMock(return_value=SimpleNamespace(text="ASK")))
    _register(partial(_post_gateway_admission, ctx, runtime))
    runner = _runner()
    request = _event("Can you check the release?")
    assert "yes or no" in await runner._handle_message(request)
    decline = _event("no")
    decline.message_id = "decline"
    assert await runner._handle_message(decline) == "agent-ok"
    forwarded = runner._handle_message_with_agent.await_args.args[0]
    assert forwarded.text == request.text and forwarded._routed_by == ("super_agent",)
    assert runtime.store.list_tickets() == []


@pytest.mark.asyncio
async def test_setup_hint_uses_the_profile_grant_and_configured_home(rig, monkeypatch):
    ctx, runtime = rig
    ctx.config.update(message_sources=["weixin"], write_message_sources=["telegram"])
    source = {"platform": "weixin", "chat_id": "inbox", "user_id": "owner"}
    async def submit(identity):
        result = await _post_gateway_admission(ctx, runtime, text="sa: run tests", source=source, message_id=identity)
        return result["reply"]

    assert "writable notification" in await submit("no-home")
    monkeypatch.setattr(ctx, "home_channel_platforms", lambda **kwargs: ["telegram"])
    assert "writable notification" not in await submit("home-ready")
    monkeypatch.setattr(ctx, "gateway_injection_allowed", lambda: False)
    assert "allow_gateway_injection" in await submit("grant-needed")


@pytest.mark.asyncio
async def test_new_task_replay_and_withdrawal_preserve_actor_and_original_question(rig):
    from unittest.mock import AsyncMock
    ctx, runtime = rig
    classify = AsyncMock(return_value=SimpleNamespace(text="NEW"))
    ctx.llm = SimpleNamespace(acomplete=classify)
    source = {"platform": "telegram", "chat_type": "group", "chat_id": "room"}

    async def send(actor, text, identity):
        return await _post_gateway_admission(ctx, runtime, source=source, session_key="g", actor_id=actor,
                                             text=text, message_id=identity)

    receipt = await send("alice", "Fix the login form", "one")
    ticket, = runtime.store.list_tickets()
    assert ticket["id"] in receipt["reply"]
    await send("alice", "Fix the login form", "one")
    classify.assert_awaited_once()
    classify.return_value = SimpleNamespace(text="STOP " + ticket["id"])
    await send("bob", "Please stop that job", "two")
    assert runtime.store.get_ticket(ticket["id"])["status"] == "queued"
    assert ticket["id"] in (await send("alice", "Stop the job", "three"))["reply"]
    assert runtime.store.get_ticket(ticket["id"])["status"] == "cancelled"
    classify.return_value = SimpleNamespace(text="NEW")
    replay = await send("alice", "Stop the job", "three")
    assert replay["action"] == "handled" and len(runtime.store.list_tickets()) == 1
    choice = await send("alice", "yes", "four")
    assert choice["action"] == "continue" and choice["text"] == "Fix the login form"


@pytest.mark.asyncio
@pytest.mark.parametrize(("read", "write"), [(False, False), (True, False), (False, True), (True, True)])
async def test_source_permissions_on_real_channel_history(rig, tmp_path, read, write):
    from functools import partial
    from unittest.mock import AsyncMock
    from gateway.session import SessionStore
    from plugins.super_agent.chat import route_chat
    from tests.gateway.test_post_gateway_admission import _event, _register, _runner

    ctx, runtime = rig
    ctx.config.update(message_sources=["whatsapp"] if read else [], write_message_sources=["whatsapp"] if write else [])
    _register(partial(_post_gateway_admission, ctx, runtime))
    runner = _runner()
    runner.session_store = SessionStore(tmp_path / "sessions", runner.config)
    event = _event("sa: check the installer")
    source = event.source

    def history(channel):
        entry = runner.session_store.lookup_by_session_key(runner._session_key_for_source(channel))
        if entry is None:
            return []
        rows = runner.session_store.persistence.messages.read_transcript(entry.session_id)
        return [(row.role, row.content) for row in rows]

    try:
        response = await runner._handle_message(event)
        tickets = runtime.store.list_tickets()
        if not read:
            assert response == "agent-ok" and not tickets
            assert "disabled" in await _command(ctx, runtime, "new forbidden", source=source)
            await route_chat(ctx, runtime, dict(source=source.to_dict(), actor_id=source.user_id,
                                                session_key="test", text="forbidden", message_id="no"), action="offer")
            assert not runtime.store.list_tickets()
        else:
            ticket, = tickets
            assert ticket["task"] == "check the installer"
            from gateway.platforms.base import EphemeralReply
            assert isinstance(response, EphemeralReply) and ticket["id"] in response
            assert ("writable notification" in response) == (not write)
            runner._handle_message_with_agent.assert_not_awaited()
            if not write:
                ctx.llm = SimpleNamespace(acomplete=AsyncMock(return_value=SimpleNamespace(text=ticket["id"])))
                followup = _event("Also test the offline package")
                followup.message_id = "m2"
                assert await runner._handle_message(followup) is None
                assignment = runtime.store.claim_next()
                assert followup.text in runtime.store.create_followup_turn(assignment["id"])["text"]
                runner._handle_message_with_agent.assert_not_awaited()
        expected = [("user", event.text)] if read and write else []
        assert history(source) == expected
        if read and write:
            other = _event("sa: check another project")
            other.source.chat_id = "another-chat"
            await runner._handle_message(other)
            assert history(other.source) == [("user", other.text)]
            assert history(source) == expected
        slash = _event("/super-agent new verify the build")
        slash.message_id = "slash-request"
        for _ in range(2):
            reply = await runner._hm_run_plugin_handler(partial(_command, ctx, runtime), slash, source)
        if read:
            assert len(runtime.store.list_tickets()) == 2 + int(write)
            assert "already queued" in reply
            if write:
                expected.append(("user", slash.text))
            assert history(source) == expected
        else:
            assert "disabled" in reply and not runtime.store.list_tickets()
    finally:
        runner.session_store.close_all_db_handles()
