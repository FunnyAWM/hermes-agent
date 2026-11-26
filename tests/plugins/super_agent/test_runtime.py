"""Router scheduling and Gateway RPC behavior."""

import asyncio
from contextlib import asynccontextmanager, suppress
from types import SimpleNamespace

import pytest

from plugins.super_agent.runtime import SuperAgentRuntime
from plugins.super_agent.store import SuperAgentStore


class _Context:
    plugin_id = "super_agent"

    def __init__(self, config=None):
        self.config = config or {}
        self.posts = []

    def get_config(self, name, default=None):
        return self.config.get(name, default)

    async def apost_message(self, text, *, source):
        self.posts.append((text, source))
        return SimpleNamespace(ok=True, error=None)


def _submit(store, event_id, source=None):
    return store.submit(
        task=f"task {event_id}", sandbox="workspace-write", workspace_root="workspace",
        source_instance="test", external_event_id=event_id, source=source,
    )[0]


def test_runtime_validates_worker_count_and_model_configuration(tmp_path):
    store = SuperAgentStore(tmp_path / "data", "default")
    runtime = SuperAgentRuntime(_Context({
        "max_concurrent_workers": "3",
        "worker_provider": " openai ",
        "worker_model": " gpt-test ",
    }), store)
    assert runtime.max_workers == 3
    assert runtime.worker_model() == ("openai", "gpt-test")

    for invalid in (0, 9, -1, 2.0, "2.0", "02", "", True, "two"):
        candidate = SuperAgentRuntime(_Context({"max_concurrent_workers": invalid}), store)
        with pytest.raises(ValueError, match="max_concurrent_workers"):
            _ = candidate.max_workers
    with pytest.raises(ValueError, match="worker_provider"):
        SuperAgentRuntime(_Context({"worker_provider": 7}), store).worker_model()


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", range(1, 9))
async def test_router_fills_configured_slots_and_refills_a_free_slot(tmp_path, monkeypatch, limit):
    store = SuperAgentStore(tmp_path / "data", "default")
    tickets = [_submit(store, str(index)) for index in range(limit + 1)]
    runtime = SuperAgentRuntime(_Context({"max_concurrent_workers": str(limit)}), store)
    started = []
    full = asyncio.Event()
    refilled = asyncio.Event()
    dispatch_finished = asyncio.Event()
    gates = {ticket["id"]: asyncio.Event() for ticket in tickets}

    async def deliver_one():
        dispatch_finished.set()
        return False

    async def run_assignment(assignment):
        started.append(assignment["ticket_id"])
        if len(started) == limit:
            full.set()
        if len(started) == limit + 1:
            refilled.set()
        await gates[assignment["ticket_id"]].wait()

    monkeypatch.setattr(runtime, "_run_assignment", run_assignment)
    monkeypatch.setattr(runtime, "_deliver_one", deliver_one)
    runner = asyncio.create_task(runtime._run())
    try:
        await asyncio.wait_for(full.wait(), 5)
        await asyncio.wait_for(dispatch_finished.wait(), 5)
        assert set(started) == {ticket["id"] for ticket in tickets[:limit]}
        assert store.get_ticket(tickets[-1]["id"])["status"] == "queued"

        gates[tickets[0]["id"]].set()
        await asyncio.wait_for(refilled.wait(), 5)
        assert started[-1] == tickets[-1]["id"]
    finally:
        runtime.stop()
        with suppress(asyncio.CancelledError):
            await runner


class _Gateway:
    def __init__(self, *, session_id="worker-1"):
        self.session_id = session_id
        self.calls = []

    async def rpc(self, method, **params):
        self.calls.append((method, params))
        if method == "session.create":
            return {"session_id": self.session_id}
        if method == "prompt.submit":
            return {"admission_id": "admission-1"}
        if method == "prompt.receipt":
            return {
                "status": "terminal",
                "outcome": "completed",
                "result": {"final_response": "done", "failed": False, "interrupted": False},
            }
        return {}


@pytest.mark.asyncio
@pytest.mark.parametrize("overrides", [
    {}, {"provider": "test-provider"}, {"model": "test-model"},
    {"provider": "test-provider", "model": "test-model"},
])
async def test_assignment_creates_managed_worker_and_submits_stable_finite_turn(tmp_path, monkeypatch, overrides):
    store = SuperAgentStore(tmp_path / "data", "default")
    ticket = _submit(store, "event", {"platform": "telegram", "chat_id": "42"})
    assignment = store.claim_next()
    runtime = SuperAgentRuntime(_Context({f"worker_{key}": value for key, value in overrides.items()}), store)
    gateway = _Gateway()
    workspace = tmp_path / "worker"
    workspace.mkdir()

    @asynccontextmanager
    async def connect(**kwargs):
        assert kwargs == {"purpose": "managed-session", "subject": "plugin:super_agent"}
        yield gateway

    async def ensure_worktree(_assignment):
        return workspace

    monkeypatch.setattr("plugins.super_agent.runtime.connect_gateway", connect)
    monkeypatch.setattr(runtime, "_sandbox_preflight", lambda: None)
    monkeypatch.setattr(runtime, "_ensure_worktree", ensure_worktree)

    await runtime._run_assignment(assignment)

    create = gateway.calls[0]
    assert create == ("session.create", {
        "request_id": f"super-agent:{assignment['id']}",
        "source": "managed_worker",
        "cwd": str(workspace),
        "cwd_explicit": True,
        "toolsets": ["terminal"],
        "title": f"Super Agent {ticket['id']}",
        **overrides,
    })
    submit = next(params for method, params in gateway.calls if method == "prompt.submit")
    assert submit["input_id"] == f"super-agent:{ticket['id']}:1:1"
    assert submit["finite"] is True and submit["unattended"] is True
    stored = store.get_ticket(ticket["id"])
    assert stored["status"] == "succeeded"
    assert stored["assignment"]["worker_session_id"] == "worker-1"


@pytest.mark.asyncio
async def test_existing_worker_is_resumed_without_a_second_create(tmp_path, monkeypatch):
    store = SuperAgentStore(tmp_path / "data", "default")
    ticket = _submit(store, "event")
    assignment = store.claim_next()
    store.bind_worker_session(assignment["id"], "worker-existing")
    assignment["worker_session_id"] = "worker-existing"
    runtime = SuperAgentRuntime(_Context({"worker_provider": "new-provider", "worker_model": "new-model"}), store)
    gateway = _Gateway(session_id="unused")

    @asynccontextmanager
    async def connect(**_kwargs):
        yield gateway

    async def ensure_worktree(_assignment):
        return tmp_path

    monkeypatch.setattr("plugins.super_agent.runtime.connect_gateway", connect)
    monkeypatch.setattr(runtime, "_sandbox_preflight", lambda: None)
    monkeypatch.setattr(runtime, "_ensure_worktree", ensure_worktree)

    await runtime._run_assignment(assignment)

    assert gateway.calls[0] == ("session.resume", {"session_id": "worker-existing"})
    assert all(method != "session.create" for method, _params in gateway.calls)
    assert store.get_ticket(ticket["id"])["status"] == "succeeded"


@pytest.mark.asyncio
async def test_missing_sandbox_backend_fails_ticket_before_gateway_create(tmp_path, monkeypatch):
    store = SuperAgentStore(tmp_path / "data", "default")
    ticket = _submit(store, "event")
    assignment = store.claim_next()
    runtime = SuperAgentRuntime(_Context(), store)
    connected = []

    @asynccontextmanager
    async def connect(**_kwargs):
        connected.append(True)
        yield _Gateway()

    monkeypatch.setattr("plugins.super_agent.runtime.connect_gateway", connect)
    monkeypatch.setattr("plugins.super_agent.runtime.settings", lambda: SimpleNamespace(enabled=True))
    monkeypatch.setattr("plugins.super_agent.runtime.resolve_binary", lambda _cfg: None)

    await runtime._run_assignment(assignment)

    assert connected == []
    assert store.get_ticket(ticket["id"])["status"] == "failed"
    assert "sandctl" in store.get_ticket(ticket["id"])["last_error"]


@pytest.mark.asyncio
async def test_cancel_persists_then_controls_the_current_worker(tmp_path, monkeypatch):
    store = SuperAgentStore(tmp_path / "data", "default")
    ticket = _submit(store, "event")
    assignment = store.claim_next()
    turn = store.ensure_initial_turn(assignment["id"], ticket["task"])
    store.mark_turn_submitted(turn["id"], "admission-1")
    store.bind_worker_session(assignment["id"], "worker-1")
    runtime = SuperAgentRuntime(_Context(), store)
    gateway = _Gateway()

    @asynccontextmanager
    async def connect(**_kwargs):
        yield gateway

    monkeypatch.setattr("plugins.super_agent.runtime.connect_gateway", connect)
    monkeypatch.setattr(runtime, "wake", lambda: None)

    await runtime.cancel(ticket["id"])

    assert store.get_ticket(ticket["id"])["status"] == "cancelled"
    assert gateway.calls == [
        ("session.resume", {"session_id": "worker-1"}),
        ("prompt.cancel", {"session_id": "worker-1", "admission_id": "admission-1"}),
    ]


@pytest.mark.asyncio
async def test_outbox_delivery_uses_original_source_and_records_receipt(tmp_path):
    store = SuperAgentStore(tmp_path / "data", "default")
    source = {"platform": "telegram", "chat_id": "42", "thread_id": "7"}
    ticket = _submit(store, "event", source)
    assignment = store.claim_next()
    store.fail_assignment(assignment["id"], "boom")
    ctx = _Context()
    runtime = SuperAgentRuntime(ctx, store)

    assert await runtime._deliver_one() is True

    assert ctx.posts == [(f"Super Agent {ticket['id']} failed: boom", source)]
    with store._connect() as conn:
        row = dict(conn.execute("SELECT status FROM outbox").fetchone())
    assert row["status"] == "delivered"


@pytest.mark.asyncio
async def test_pause_keeps_running_worker_and_defers_queued_work_until_resume(tmp_path, monkeypatch):
    store = SuperAgentStore(tmp_path / "data", "default")
    first = _submit(store, "first")
    runtime = SuperAgentRuntime(_Context(), store)
    loop = asyncio.get_running_loop()
    observed_states = asyncio.Queue()
    original_is_paused = store.is_paused
    first_started = asyncio.Event()
    second_started = asyncio.Event()
    gates = {first["id"]: asyncio.Event()}

    def observed_is_paused():
        value = original_is_paused()
        loop.call_soon_threadsafe(observed_states.put_nowait, value)
        return value

    async def run_assignment(assignment):
        event = first_started if assignment["ticket_id"] == first["id"] else second_started
        event.set()
        await gates[assignment["ticket_id"]].wait()

    async def wait_for_state(expected):
        while await asyncio.wait_for(observed_states.get(), 5) is not expected:
            pass

    monkeypatch.setattr(store, "is_paused", observed_is_paused)
    monkeypatch.setattr(runtime, "_run_assignment", run_assignment)
    runner = asyncio.create_task(runtime._run())
    try:
        await asyncio.wait_for(first_started.wait(), 5)
        while not observed_states.empty():
            observed_states.get_nowait()
        assert store.set_paused(True)
        second = _submit(store, "second")
        gates[second["id"]] = asyncio.Event()
        runtime._wake.set()
        await wait_for_state(True)
        assert not second_started.is_set()

        gates[first["id"]].set()
        await wait_for_state(True)
        assert not second_started.is_set()

        assert store.set_paused(False)
        runtime._wake.set()
        await asyncio.wait_for(second_started.wait(), 5)
    finally:
        for gate in gates.values():
            gate.set()
        runtime.stop()
        await asyncio.wait_for(runner, 5)


@pytest.mark.asyncio
async def test_takeover_cancels_current_admission_once_and_retains_worker(tmp_path, monkeypatch):
    store = SuperAgentStore(tmp_path / "data", "default")
    ticket = _submit(store, "event")
    assignment = store.claim_next()
    turn = store.ensure_initial_turn(assignment["id"], ticket["task"])
    store.mark_turn_submitted(turn["id"], "admission-1")
    store.bind_worker_session(assignment["id"], "worker-1")
    before = store.get_ticket(ticket["id"])["assignment"]
    runtime = SuperAgentRuntime(_Context(), store)
    gateway = _Gateway()

    @asynccontextmanager
    async def connect(**_kwargs):
        yield gateway

    monkeypatch.setattr("plugins.super_agent.runtime.connect_gateway", connect)
    monkeypatch.setattr(runtime, "wake", lambda: None)

    assert await runtime.takeover(ticket["id"]) is not None
    assert await runtime.takeover(ticket["id"]) is None

    assert gateway.calls == [
        ("session.resume", {"session_id": "worker-1"}),
        ("prompt.cancel", {"session_id": "worker-1", "admission_id": "admission-1"}),
    ]
    assert all(method not in {"session.delete", "session.reset"} for method, _params in gateway.calls)
    after = store.get_ticket(ticket["id"])["assignment"]
    assert after["status"] == "taken_over"
    assert (after["worker_session_id"], after["workspace"], after["sandbox"]) == (
        before["worker_session_id"], before["workspace"], before["sandbox"],
    )
    assert [event["action"] for event in store.list_control_events()] == ["takeover"]


@pytest.mark.asyncio
async def test_watchdog_loop_scans_independently_and_logs_new_alert(tmp_path, monkeypatch, caplog):
    store = SuperAgentStore(tmp_path / "data", "default")
    runtime = SuperAgentRuntime(_Context(), store)
    runtime.watchdog_seconds = 4
    loop = asyncio.get_running_loop()
    scanned = asyncio.Event()

    def scan(threshold):
        assert threshold == 4
        loop.call_soon_threadsafe(scanned.set)
        return [{"ticket_id": "sa-stale", "detail": "no progress"}]

    monkeypatch.setattr(store, "scan_watchdog", scan)
    runtime._recovered.set()
    task = asyncio.create_task(runtime._run_watchdog())
    try:
        await asyncio.wait_for(scanned.wait(), 5)
        runtime._closing = True
        runtime._watchdog_wake.set()
        await asyncio.wait_for(task, 5)
    finally:
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
    assert "sa-stale" in caplog.text and "no progress" in caplog.text


@pytest.mark.asyncio
async def test_stop_cancels_router_watchdog_and_worker_tasks(tmp_path):
    runtime = SuperAgentRuntime(_Context(), SuperAgentStore(tmp_path / "data", "default"))
    blocker = asyncio.Event()
    runtime._runner = asyncio.create_task(blocker.wait())
    runtime._watchdog = asyncio.create_task(blocker.wait())
    worker = asyncio.create_task(blocker.wait())
    runtime._workers.add(worker)

    runtime.stop()
    for task in (runtime._runner, runtime._watchdog, worker):
        with suppress(asyncio.CancelledError):
            await task

    assert runtime._closing is True
    assert runtime._runner.cancelled()
    assert runtime._watchdog.cancelled()
    assert worker.cancelled()
