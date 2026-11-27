"""Inputs and incomplete Worker turns survive restart, retrial, and cancellation."""

import asyncio
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from plugins.super_agent.runtime import SuperAgentRuntime
from plugins.super_agent.store import SuperAgentStore
from plugins.super_agent.worker_input import remove_owned_input, worker_prompt
from plugins.super_agent.worker_turn import execute_turn
from tests.plugins.super_agent.test_runtime import _Context, _Gateway, _submit


@pytest.mark.asyncio
async def test_long_initial_and_followup_are_readable_from_the_same_worktree(tmp_path):
    store = SuperAgentStore(tmp_path / "data", "default")
    workspace = tmp_path / "worktree"
    workspace.mkdir()
    task = "a" * 9000 + "initial-tail"
    followup = "b" * 9000 + "followup-tail"
    ticket, _ = store.submit(task=task, sandbox="read-only", workspace_root=str(tmp_path),
                             source_instance="local", external_event_id="1", source=None)
    assignment = store.claim_next()
    store.add_inbox(ticket["id"], followup)
    gateway = _Gateway()
    await SuperAgentRuntime(_Context(), store)._run_turns(gateway, assignment, "worker", workspace=workspace)
    prompts = [params["text"] for method, params in gateway.calls if method == "prompt.submit"]
    assert len(prompts) == 2
    for prompt, expected in zip(prompts, (task, followup)):
        ref = next(word for word in prompt.split() if word.startswith(".super-agent-input-"))
        assert (workspace / ref).read_text(encoding="utf-8").endswith(expected)
    assert store.get_ticket(ticket["id"])["status"] == "succeeded"


def test_legacy_input_replay_keeps_payload_and_cleanup_preserves_replacements(tmp_path):
    ticket_id = "sa-aabbccddeeff"
    body = "goal" * 2600 + "required final constraint"
    ref = f"spill/{ticket_id}/001122334455.txt"
    old = body[:8000] + f"\n\n[Full content spilled to {ref}; read it from the workspace before continuing.]"
    turn = {"id": "turn-aabbccddeeff", "sequence": 1, "text": old}
    proof = []
    assert worker_prompt(tmp_path, turn, body, ticket_id, lambda *args: proof.append(args)) == old
    assert (tmp_path / ref).read_text(encoding="utf-8") == body
    assert worker_prompt(tmp_path, turn, body, ticket_id, lambda *args: proof.append(args)) == old
    row, = (dict(zip(("turn_id", "path", "device", "inode", "digest"), record)) for record in proof)
    (tmp_path / ref).write_text("operator edit", encoding="utf-8")
    remove_owned_input(tmp_path, row)
    assert (tmp_path / ref).read_text(encoding="utf-8") == "operator edit"
    with pytest.raises(ValueError, match="conflicts"):
        worker_prompt(tmp_path, turn, body, ticket_id, lambda *args: proof.append(args))


@pytest.mark.asyncio
async def test_dispatch_recovers_and_refills_during_slow_delivery(tmp_path, monkeypatch):
    store = SuperAgentStore(tmp_path, "default")
    first, second = [_submit(store, name)["id"] for name in ("first", "second")]
    runtime = SuperAgentRuntime(_Context({"max_concurrent_workers": "1"}), store)
    started = asyncio.Queue()
    gates = {key: asyncio.Event() for key in (first, second)}
    sending = asyncio.Event()
    release_send = asyncio.Event()
    retrying = asyncio.Event()
    loop = asyncio.get_running_loop()
    recover = store.recover
    attempts = iter([False, True])

    def flaky_recover():
        if not next(attempts):
            loop.call_soon_threadsafe(retrying.set)
            raise sqlite3.OperationalError("locked")
        recover()

    async def slow_delivery():
        sending.set()
        await release_send.wait()
        return False

    async def worker(assignment):
        key = assignment["ticket_id"]
        await started.put(key)
        await gates[key].wait()

    monkeypatch.setattr(store, "recover", flaky_recover)
    monkeypatch.setattr(runtime, "_deliver_one", slow_delivery)
    monkeypatch.setattr(runtime, "_run_assignment", worker)
    router = asyncio.create_task(runtime._run())
    try:
        await asyncio.wait_for(retrying.wait(), 5)
        runtime._wake.set()
        assert await asyncio.wait_for(started.get(), 5) == first
        await asyncio.wait_for(sending.wait(), 5)
        gates[first].set()
        assert await asyncio.wait_for(started.get(), 5) == second
    finally:
        gates[second].set()
        release_send.set()
        runtime.stop()
        await asyncio.wait_for(router, 5)


@pytest.mark.parametrize("operation", ["fail", "cancel", "takeover"])
def test_retry_reclaims_old_followup_and_ignores_late_settlement(tmp_path, operation):
    store = SuperAgentStore(tmp_path, "default")
    ticket = _submit(store, "first")
    first = store.claim_next()
    store.mark_turn_terminal(store.ensure_initial_turn(first["id"], "first")["id"], "done")
    store.add_inbox(ticket["id"], "outstanding")
    old = store.create_followup_turn(first["id"])
    store.mark_turn_submitted(old["id"], "old")
    if operation == "fail":
        store.fail_assignment(first["id"], "failed")
    else:
        getattr(store, operation)(ticket["id"])
    assert store.retry(ticket["id"])
    second = store.claim_next()
    store.mark_turn_terminal(old["id"], "late")
    store.mark_turn_terminal(store.ensure_initial_turn(second["id"], "first")["id"], "done")
    followup = store.create_followup_turn(second["id"])
    assert "outstanding" in followup["text"]
    store.mark_turn_terminal(followup["id"], "done again")
    assert store.complete_if_idle(second["id"], "done again")


@pytest.mark.asyncio
async def test_expired_turn_cancels_same_admission_after_recovery(tmp_path, monkeypatch):
    store = SuperAgentStore(tmp_path, "default")
    ticket = _submit(store, "deadline")
    assignment = store.claim_next()
    turn = store.ensure_initial_turn(assignment["id"], "first")
    store.bind_worker_session(assignment["id"], "worker")
    monkeypatch.setattr("plugins.super_agent.store._now", lambda: 100)
    original = store.start_turn(turn["id"], 30)
    store.mark_turn_submitted(turn["id"], "accepted")
    store.recover()
    resumed = store.claim_next()
    turn = store.start_turn(turn["id"], 0)
    assert turn["deadline_at"] == original["deadline_at"] == 130
    gateway = SimpleNamespace(rpc=AsyncMock(return_value={"status": "started"}))
    with pytest.raises(RuntimeError):
        await execute_turn(store, gateway, "worker", turn, "first", 0)
    gateway.rpc.assert_awaited_once_with("prompt.cancel", session_id="worker", admission_id="accepted")
    assert store.assignment_sandbox("worker") is None
    store.fail_assignment(resumed["id"], "timeout")
    assert store.retry(ticket["id"])
    assert store.claim_next() is None
    stop, = store.pending_stops()
    store.confirm_stopped(stop["id"])
    assert store.claim_next()["generation"] == 2


@pytest.mark.asyncio
async def test_unknown_receipt_confirms_the_lost_worker_so_retry_can_claim(tmp_path):
    store = SuperAgentStore(tmp_path, "default")
    ticket = _submit(store, "lost")
    assignment = store.claim_next()
    turn = store.ensure_initial_turn(assignment["id"], "first")
    store.bind_worker_session(assignment["id"], "worker")
    started = store.start_turn(turn["id"], 0)
    store.mark_turn_submitted(turn["id"], "admission")
    started = {**started, "admission_id": "admission"}
    gateway = SimpleNamespace(rpc=AsyncMock(return_value={"status": "unknown", "outcome": "unknown"}))
    with pytest.raises(RuntimeError, match="outcome is unknown"):
        await execute_turn(store, gateway, "worker", started, "first", 0)
    assert store.pending_stops() == []
    store.fail_assignment(assignment["id"], "lost")
    assert store.retry(ticket["id"])
    assert store.claim_next()["generation"] == 2


@pytest.mark.asyncio
async def test_watchdog_uses_current_worker_activity_without_receipt_heartbeats(tmp_path, monkeypatch):
    store = SuperAgentStore(tmp_path, "default")
    _submit(store, "watchdog")
    monkeypatch.setattr("plugins.super_agent.store._now", lambda: 100)
    assignment = store.claim_next()
    turn = store.ensure_initial_turn(assignment["id"], "first")
    store.mark_turn_submitted(turn["id"], "admission")
    store.bind_worker_session(assignment["id"], "worker")
    monkeypatch.setattr("plugins.super_agent.store._now", lambda: 150)
    assert store.scan_watchdog(20)
    runtime = SuperAgentRuntime(_Context(), store)
    events = asyncio.Queue()
    written = asyncio.Event()
    loop = asyncio.get_running_loop()
    record = store.note_worker_progress

    def recording(session_id, admission_id):
        record(session_id, admission_id)
        loop.call_soon_threadsafe(written.set)

    monkeypatch.setattr(store, "note_worker_progress", recording)
    listener = asyncio.create_task(runtime._consume_progress(events, "worker"))
    try:
        await events.put({"params": {"type": "tool.complete", "session_id": "worker", "admission_id": "admission"}})
        await asyncio.wait_for(written.wait(), 5)
        store.scan_watchdog(20)
        assert store.list_watchdog_alerts(active_only=True) == []
    finally:
        listener.cancel()
        with pytest.raises(asyncio.CancelledError):
            await listener
