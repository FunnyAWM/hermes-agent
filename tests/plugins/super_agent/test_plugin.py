"""Super Agent command, admission, and real discovery behavior."""

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

    async def tell(self, ticket_id, text):
        return self.store.add_inbox(ticket_id, text)

    async def cancel(self, ticket_id):
        return self.store.cancel(ticket_id)

    async def takeover(self, ticket_id):
        return self.store.takeover(ticket_id)

    async def retry(self, ticket_id):
        return self.store.retry(ticket_id)

    async def pause(self):
        return self.store.set_paused(True)

    async def resume(self):
        return self.store.set_paused(False)

    async def fanout(self, _source_instance, _thread_key, _text):
        return []


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
    assert ordinary["action"] == "handled"
    rejected = await _post_gateway_admission(
        ctx, runtime, text="sa:", source=source, message_id="message-3"
    )
    assert rejected["action"] == "handled" and "rejected" in rejected["reply"]
    assert runtime.store.counts() == {"queued": 2}


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

    assert await _post_gateway_admission(ctx, runtime, text="preview", message_id="2", **channel) is None
    assert [row["detail"] for row in runtime.store.list_observe_log()] == ["preview"]
    assert runtime.store.create_followup_turn(assignment["id"]) is None

    await _command(ctx, runtime, f"observe {thread_key} auto", **channel)
    other = await _post_gateway_admission(
        ctx, runtime, text="other conversation", message_id="other-3", source=source, session_key="other-session"
    )
    assert other["action"] == "handled"
    assert runtime.store.create_followup_turn(assignment["id"]) is None
    forwarded = await _post_gateway_admission(ctx, runtime, text="count blue items", message_id="3", **channel)
    assert forwarded and forwarded["action"] == "handled"
    turn = runtime.store.create_followup_turn(assignment["id"])
    assert "count blue items" in turn["text"]
    runtime.store.mark_turn_terminal(turn["id"], "counted")
    await _command(ctx, runtime, f"unsubscribe {ticket_id} {thread_key}", **channel)
    independent = await _post_gateway_admission(ctx, runtime, text="ordinary", message_id="4", **channel)
    assert "queued" in independent["reply"]
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
    assert (ordinary is None) == (expected is None)
    reply = await _post_gateway_admission(ctx, runtime, text="sa: next", message_id="3", source=source)
    if expected is None:
        assert reply is None
    else:
        assert reply["action"] == "handled" and expected in reply["reply"]
    tasks = {row["task"] for row in runtime.store.list_tickets()}
    assert tasks == ({"original", "next", "hello"} if expected == "queued" else {"original"})


@pytest.mark.asyncio
async def test_plain_tasks_without_message_ids_are_distinct_and_controls_stay_local(rig):
    ctx, runtime = rig
    source = {"platform": "discord", "chat_id": "room"}
    for _ in range(2):
        result = await _post_gateway_admission(ctx, runtime, text="run again", source=source)
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

    monkeypatch.setenv("HERMES_LANGUAGE", "zh")
    reset_language_cache()
    ctx, runtime = rig
    assert "超级代理命令" in await _command(ctx, runtime, "help")
    reply = await _post_gateway_admission(ctx, runtime, text="运行测试", source={"platform": "weixin", "chat_id": "1"})
    assert "已入队" in reply["reply"]
    ticket, = runtime.store.list_tickets()
    assert "任务：运行测试" in await _command(ctx, runtime, f"status {ticket['id']}")


@pytest.mark.asyncio
async def test_discovered_router_consumes_whatsapp_text_before_channel_agent(discovered_super_agent):
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
    plugins, manager, home = discovered_super_agent
    [row] = [plugin for plugin in manager.list_plugins() if plugin["key"] == "super_agent"]
    assert row["enabled"] is True and not row.get("error"), row
    assert plugins.get_plugin_command_handler("super-agent") is not None
    assert manager.has_hook("post_gateway_admission")
    assert len(list((home / "plugin-data").glob("*/super_agent.db"))) == 1

    assert manager.unload("super_agent") is True
    assert plugins.get_plugin_command_handler("super-agent") is None
    assert not manager.has_hook("post_gateway_admission")
