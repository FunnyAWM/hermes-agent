"""Behavior contracts for the durable Super Agent state machine."""

from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import threading

import pytest

from plugins.super_agent.store import SuperAgentStore


def _store(tmp_path, profile="default"):
    return SuperAgentStore(tmp_path / "super-agent", profile)


def _submit(store, event_id="event-1", *, source=True, sandbox="workspace-write"):
    return store.submit(
        task="修复登录问题",
        sandbox=sandbox,
        workspace_root="workspace",
        source_instance="telegram:bot-1",
        external_event_id=event_id,
        source={"chat_id": "群聊-42", "thread_id": "主题-7"} if source else None,
    )


def _rows(store, sql, params=()):
    with sqlite3.connect(store.path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def test_submit_is_profile_scoped_idempotent_and_validates_before_writing(tmp_path):
    store = _store(tmp_path, "alpha")
    barrier = threading.Barrier(6)

    def submit_once(_):
        barrier.wait()
        return _submit(store)

    with ThreadPoolExecutor(max_workers=6) as pool:
        submissions = list(pool.map(submit_once, range(6)))

    assert len({ticket["id"] for ticket, _created in submissions}) == 1
    assert sum(created for _ticket, created in submissions) == 1
    assert {table: len(_rows(store, f"SELECT * FROM {table}")) for table in ("triggers", "tickets", "effects")} == {
        "triggers": 1,
        "tickets": 1,
        "effects": 1,
    }
    trigger = _rows(store, "SELECT source_json FROM triggers")[0]
    assert json.loads(trigger["source_json"]) == {"chat_id": "群聊-42", "thread_id": "主题-7"}

    other = _store(tmp_path, "beta")
    other_ticket, created = _submit(other)
    alpha_ticket = submissions[0][0]
    assert created is True
    assert other_ticket["id"] != alpha_ticket["id"]
    assert store.get_ticket(other_ticket["id"]) is None
    assert other.get_ticket(alpha_ticket["id"]) is None

    before = {table: len(_rows(store, f"SELECT * FROM {table}")) for table in ("triggers", "tickets", "effects")}
    for task, sandbox in ((" ", "workspace-write"), ("task", "full-access"), ("task", "unknown")):
        with pytest.raises(ValueError):
            store.submit(
                task=task,
                sandbox=sandbox,
                workspace_root="workspace",
                source_instance="telegram:bot-1",
                external_event_id=f"invalid-{sandbox}",
                source=None,
            )
    assert {table: len(_rows(store, f"SELECT * FROM {table}")) for table in before} == before


def test_claim_is_ordered_and_atomic_across_store_connections(tmp_path):
    store = _store(tmp_path)
    first, _ = _submit(store, "event-1")
    second, _ = _submit(store, "event-2")

    assert store.claim_next()["ticket_id"] == first["id"]
    store_a, store_b = _store(tmp_path), _store(tmp_path)
    barrier = threading.Barrier(2)

    def claim(candidate):
        barrier.wait()
        return candidate.claim_next()

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(claim, (store_a, store_b)))
    winners = [claim for claim in claims if claim is not None]
    assert len(winners) == 1
    assert winners[0]["ticket_id"] == second["id"]
    assert store.claim_next() is None


def test_turn_identity_submission_and_session_sandbox_survive_reopen(tmp_path):
    store = _store(tmp_path)
    ticket, _ = _submit(store)
    assignment = store.claim_next()
    turn = store.ensure_initial_turn(assignment["id"], ticket["task"])

    assert store.ensure_initial_turn(assignment["id"], "ignored") == turn
    assert turn["input_id"] == f"super-agent:{ticket['id']}:1:1"
    store.mark_turn_submitted(turn["id"], "admission-1")
    stored_assignment = store.get_ticket(ticket["id"])["assignment"]
    assert stored_assignment["admission_id"] == "admission-1"
    assert store.pending_turn(assignment["id"])["admission_id"] == "admission-1"

    store.bind_worker_session(assignment["id"], "worker-old")
    assert store.assignment_sandbox("worker-old") == "workspace-write"
    store.bind_worker_session(assignment["id"], "worker-current")
    assert store.assignment_sandbox("worker-old") is None
    assert store.assignment_sandbox("worker-current") == "workspace-write"

    reopened = _store(tmp_path)
    assert reopened.ensure_initial_turn(assignment["id"], "ignored")["input_id"] == turn["input_id"]
    reopened.mark_turn_terminal(turn["id"], "first result")
    reopened.mark_turn_terminal(turn["id"], "late duplicate")
    assert _rows(store, "SELECT status,result FROM turns WHERE id=?", (turn["id"],)) == [
        {"status": "terminal", "result": "first result"}
    ]


def test_recover_requeues_known_work_and_replays_uncertain_delivery(tmp_path):
    store = _store(tmp_path)
    ticket_a, _ = _submit(store, "event-a")
    ticket_b, _ = _submit(store, "event-b")
    _ticket_c, _ = _submit(store, "event-c")

    assignment_a = store.claim_next()
    turn_a = store.ensure_initial_turn(assignment_a["id"], ticket_a["task"])
    store.mark_turn_submitted(turn_a["id"], "admission-a")

    assignment_b = store.claim_next()
    first_b = store.ensure_initial_turn(assignment_b["id"], ticket_b["task"])
    store.mark_turn_terminal(first_b["id"], "ready for follow-up")
    assert store.add_inbox(ticket_b["id"], "keep this message")
    followup = store.create_followup_turn(assignment_b["id"])
    store.mark_turn_submitted(followup["id"], "admission-b")

    assignment_c = store.claim_next()
    assert store.fail_assignment(assignment_c["id"], "worker failed")
    sending = store.claim_outbox()
    assert sending is not None

    reopened = _store(tmp_path)
    reopened.recover()
    reopened.recover()

    assert _rows(store, "SELECT status FROM assignments WHERE id=?", (assignment_a["id"],))[0]["status"] == "interrupted"
    assert _rows(store, "SELECT status FROM turns WHERE id=?", (turn_a["id"],))[0]["status"] == "pending"
    assert _rows(store, "SELECT status FROM inbox WHERE ticket_id=?", (ticket_b["id"],))[0]["status"] == "pending"
    replay = reopened.claim_outbox()
    assert replay["id"] == sending["id"] and replay["attempts"] == 2

    recovered = reopened.claim_next()
    assert (recovered["id"], recovered["effect_id"]) == (assignment_a["id"], assignment_a["effect_id"])
    assert reopened.ensure_initial_turn(recovered["id"], "ignored")["input_id"] == turn_a["input_id"]


def test_cancel_retry_and_stale_assignment_cannot_revive_ticket(tmp_path):
    store = _store(tmp_path)
    queued, _ = _submit(store, "queued")
    cancelled = store.cancel(queued["id"])
    assert cancelled["status"] == "cancelled"
    notice = store.claim_outbox()
    assert notice is not None and queued["id"] in notice["text"]
    assert store.cancel(queued["id"])["status"] == "cancelled"
    assert store.claim_outbox() is None

    running, _ = _submit(store, "running")
    old_assignment = store.claim_next()
    old_turn = store.ensure_initial_turn(old_assignment["id"], running["task"])
    store.mark_turn_submitted(old_turn["id"], "old-admission")
    store.cancel(running["id"])
    assert store.retry(running["id"]) is True
    current = store.claim_next()
    assert current["generation"] == old_assignment["generation"] + 1

    assert store.complete_if_idle(old_assignment["id"], "late success") is False
    assert store.fail_assignment(old_assignment["id"], "late failure") is False
    store.mark_turn_terminal(old_turn["id"], "late receipt")
    assert store.get_ticket(running["id"])["status"] == "running"

    succeeded, _ = _submit(store, "succeeded")
    success_assignment = store.claim_next()
    assert store.complete_if_idle(success_assignment["id"], "done") is True
    assert store.retry(succeeded["id"]) is False


def test_followup_blocks_completion_until_messages_are_consumed(tmp_path):
    store = _store(tmp_path)
    ticket, _ = _submit(store)
    assignment = store.claim_next()
    first_turn = store.ensure_initial_turn(assignment["id"], ticket["task"])
    store.mark_turn_terminal(first_turn["id"], "initial result")

    assert store.add_inbox(ticket["id"], "first")
    assert store.add_inbox(ticket["id"], "second")
    assert store.complete_if_idle(assignment["id"], "too early") is False
    followup = store.create_followup_turn(assignment["id"])
    assert followup["text"] == "User follow-up:\n- first\n- second"
    assert store.create_followup_turn(assignment["id"])["id"] == followup["id"]

    store.mark_turn_submitted(followup["id"], "followup-admission")
    assert store.complete_if_idle(assignment["id"], "still early") is False
    store.mark_turn_terminal(followup["id"], "follow-up result")
    assert [row["status"] for row in _rows(store, "SELECT status FROM inbox ORDER BY id")] == [
        "consumed",
        "consumed",
    ]
    assert store.complete_if_idle(assignment["id"], "finished") is True
    assert store.complete_if_idle(assignment["id"], "duplicate") is False
    assert store.add_inbox(ticket["id"], "too late") is False

    notice = store.claim_outbox()
    assert notice is not None
    assert json.loads(notice["source_json"])["thread_id"] == "主题-7"
    assert "initial result" in notice["text"]
    store.settle_outbox(notice["id"], delivered=True)
    followup_notice = store.claim_outbox()
    assert "follow-up result" in followup_notice["text"]
    store.settle_outbox(followup_notice["id"], delivered=True)
    assert store.claim_outbox() is None


def test_outbox_claim_is_atomic_and_settlement_records_outcome(tmp_path):
    store = _store(tmp_path)
    ticket, _ = _submit(store)
    assignment = store.claim_next()
    assert store.fail_assignment(assignment["id"], "boom") is True

    stores = (_store(tmp_path), _store(tmp_path))
    barrier = threading.Barrier(2)

    def claim(candidate):
        barrier.wait()
        return candidate.claim_outbox()

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(claim, stores))
    claimed = [row for row in claims if row is not None]
    assert len(claimed) == 1
    outbox = claimed[0]
    assert outbox["ticket_id"] == ticket["id"]
    assert _rows(store, "SELECT status FROM outbox WHERE id=?", (outbox["id"],))[0]["status"] == "sending"

    store.settle_outbox(outbox["id"], delivered=False, error="timeout")
    assert _rows(store, "SELECT status,error FROM outbox WHERE id=?", (outbox["id"],))[0] == {
        "status": "pending",
        "error": "timeout",
    }
    assert store.claim_outbox() is None


def test_pause_is_profile_scoped_persistent_and_blocks_claim_and_delivery(tmp_path):
    alpha = _store(tmp_path, "alpha")
    beta = _store(tmp_path, "beta")
    failed, _ = _submit(alpha, "failed")
    failed_assignment = alpha.claim_next()
    assert alpha.fail_assignment(failed_assignment["id"], "boom")
    queued, _ = _submit(alpha, "queued")
    beta_ticket, _ = _submit(beta, "queued")

    assert alpha.set_paused(True) is True
    assert alpha.set_paused(True) is False
    assert _store(tmp_path, "alpha").is_paused() is True
    assert beta.is_paused() is False
    assert alpha.claim_next() is None
    assert alpha.claim_outbox() is None
    assert beta.claim_next()["ticket_id"] == beta_ticket["id"]
    assert [event["action"] for event in alpha.list_control_events()] == ["pause"]

    assert alpha.set_paused(False) is True
    assert alpha.set_paused(False) is False
    assert alpha.claim_next()["ticket_id"] == queued["id"]
    assert alpha.claim_outbox()["ticket_id"] == failed["id"]
    assert [event["action"] for event in alpha.list_control_events()] == ["resume", "pause"]


def test_watchdog_deduplicates_without_mutating_work_and_resolves(tmp_path, monkeypatch):
    clock = {"now": 100.0}
    monkeypatch.setattr("plugins.super_agent.store._now", lambda: clock["now"])
    store = _store(tmp_path)
    ticket, _ = _submit(store)
    assignment = store.claim_next()
    turn = store.ensure_initial_turn(assignment["id"], "unchanged prompt")
    store.mark_turn_submitted(turn["id"], "admission-1")
    store.bind_worker_session(assignment["id"], "worker-1")
    assert store.add_inbox(ticket["id"], "keep pending")
    snapshot_sql = {
        "tickets": "SELECT id,status,task,sandbox,retry_count FROM tickets",
        "effects": "SELECT id,status,attempt FROM effects",
        "assignments": (
            "SELECT id,status,generation,sandbox,workspace,worker_session_id,admission_id "
            "FROM assignments"
        ),
        "turns": "SELECT id,status,input_id,text,admission_id FROM turns",
        "inbox": "SELECT id,status,text FROM inbox",
    }
    before = {table: _rows(store, sql) for table, sql in snapshot_sql.items()}

    clock["now"] = 1000.0
    [created] = store.scan_watchdog(300)
    assert created["ticket_id"] == ticket["id"]
    assert {table: _rows(store, sql) for table, sql in snapshot_sql.items()} == before
    [alert] = store.list_watchdog_alerts(active_only=False)
    assert alert["status"] == "active"
    watchdog_outbox = _rows(store, "SELECT id,status FROM outbox WHERE kind LIKE 'watchdog:%'")
    assert len(watchdog_outbox) == 1 and watchdog_outbox[0]["status"] == "pending"

    clock["now"] = 1100.0
    assert store.scan_watchdog(300) == []
    [updated] = store.list_watchdog_alerts(active_only=False)
    assert updated["id"] == alert["id"] and updated["last_seen"] == 1100.0
    assert len(_rows(store, "SELECT id FROM outbox WHERE kind LIKE 'watchdog:%'")) == 1

    clock["now"] = 1150.0
    with store._connect() as conn:
        conn.execute("UPDATE assignments SET updated_at=? WHERE id=?", (clock["now"], assignment["id"]))
        conn.commit()
    assert store.scan_watchdog(300) == []
    [resolved] = store.list_watchdog_alerts(active_only=False)
    assert resolved["status"] == "resolved" and resolved["resolved_at"] == 1150.0
    assert _rows(store, "SELECT status,error FROM outbox WHERE kind LIKE 'watchdog:%'") == [{
        "status": "failed",
        "error": "watchdog alert resolved before delivery",
    }]
    assert store.list_watchdog_alerts() == []

    clock["now"] = 1500.0
    [reopened] = store.scan_watchdog(300)
    assert reopened["id"] == assignment["id"]
    [active_again] = store.list_watchdog_alerts(active_only=False)
    assert active_again["id"] == alert["id"] and active_again["status"] == "active"
    assert len(_rows(store, "SELECT id FROM outbox WHERE kind LIKE 'watchdog:%'")) == 2

    store.mark_turn_terminal(turn["id"], "initial complete")
    followup = store.create_followup_turn(assignment["id"])
    store.mark_turn_terminal(followup["id"], "follow-up complete")
    assert store.complete_if_idle(assignment["id"], "done")
    clock["now"] = 1600.0
    assert store.scan_watchdog(300) == []
    assert store.list_watchdog_alerts() == []
    for invalid in (0, float("nan")):
        with pytest.raises(ValueError):
            store.scan_watchdog(invalid)


def test_takeover_history_and_three_retry_limit_are_immutable(tmp_path):
    store = _store(tmp_path)
    ticket, _ = _submit(store)
    first = store.claim_next()
    first_turn = store.ensure_initial_turn(first["id"], ticket["task"])
    store.mark_turn_submitted(first_turn["id"], "admission-1")
    store.bind_worker_session(first["id"], "worker-1")

    assert store.takeover(ticket["id"])["id"] == first["id"]
    assert store.takeover(ticket["id"]) is None
    original = _rows(
        store,
        "SELECT generation,sandbox,workspace,worker_session_id,admission_id,status "
        "FROM assignments WHERE id=?",
        (first["id"],),
    )[0]
    assert original["status"] == "taken_over"
    stop, = store.pending_stops()
    store.confirm_stopped(stop["id"])

    assignments = [first]
    input_ids = [first_turn["input_id"]]
    for retry_number in range(1, 4):
        assert store.retry(ticket["id"]) is True
        current = store.claim_next()
        assignments.append(current)
        current_turn = store.ensure_initial_turn(current["id"], ticket["task"])
        input_ids.append(current_turn["input_id"])
        if retry_number == 2:
            assert store.fail_assignment(current["id"], "failed again")
        else:
            assert store.takeover(ticket["id"])["id"] == current["id"]
        assert store.get_ticket(ticket["id"])["retry_count"] == retry_number

    assert store.retry(ticket["id"]) is False
    assert [row["generation"] for row in assignments] == [1, 2, 3, 4]
    assert len(set(input_ids)) == 4
    assert {row["sandbox"] for row in assignments} == {"workspace-write"}
    assert _rows(
        store,
        "SELECT generation,sandbox,workspace,worker_session_id,admission_id,status "
        "FROM assignments WHERE id=?",
        (first["id"],),
    )[0] == original
    assert store.complete_if_idle(first["id"], "late success") is False
    assert store.fail_assignment(first["id"], "late failure") is False
    store.mark_turn_terminal(first_turn["id"], "late receipt")
    assert store.get_ticket(ticket["id"])["status"] == "taken_over"
    assert store.add_inbox(ticket["id"], "too late") is False
    actions = [event["action"] for event in store.list_control_events(limit=20)]
    assert actions.count("retry") == 3 and actions.count("takeover") == 3
    assert len(_rows(store, "SELECT id FROM outbox WHERE kind='taken_over'")) == 1


def test_recover_audit_is_profile_scoped_idempotent_and_preserves_pause(tmp_path):
    alpha = _store(tmp_path, "alpha")
    beta = _store(tmp_path, "beta")
    alpha_ticket, _ = _submit(alpha, "alpha")
    beta_ticket, _ = _submit(beta, "beta")
    alpha_assignment = alpha.claim_next()
    beta_assignment = beta.claim_next()
    alpha_turn = alpha.ensure_initial_turn(alpha_assignment["id"], alpha_ticket["task"])
    beta.ensure_initial_turn(beta_assignment["id"], beta_ticket["task"])
    alpha.mark_turn_submitted(alpha_turn["id"], "alpha-admission")
    assert alpha.set_paused(True)

    alpha.recover()
    alpha.recover()
    assert alpha.is_paused() is True
    assert alpha.claim_next() is None
    assert alpha.get_ticket(alpha_ticket["id"])["status"] == "queued"
    assert _rows(alpha, "SELECT status FROM assignments WHERE id=?", (alpha_assignment["id"],)) == [
        {"status": "interrupted"}
    ]
    assert beta.get_ticket(beta_ticket["id"])["status"] == "running"
    alpha_events = alpha.list_control_events(limit=20)
    assert [event["action"] for event in alpha_events].count("restart_requeue") == 1

    assert alpha.set_paused(False)
    resumed = alpha.claim_next()
    assert resumed["id"] == alpha_assignment["id"]
    assert alpha.ensure_initial_turn(resumed["id"], "ignored")["input_id"] == alpha_turn["input_id"]

    beta.recover()
    beta.recover()
    beta_events = beta.list_control_events(limit=20)
    assert [event["action"] for event in beta_events].count("restart_requeue") == 1
    assert all(event["profile"] == "beta" for event in beta_events)


# --- SA-05: subscriptions and handback ---

def test_subscription_lifecycle_is_many_to_many_and_cleaned_on_terminal(tmp_path):
    store = _store(tmp_path)
    ticket_a, _ = _submit(store, "event-a")
    ticket_b, _ = _submit(store, "event-b")
    thread_x = store.ensure_thread("gateway:s1", "tg:chat1:topic1")
    thread_y = store.ensure_thread("gateway:s1", "tg:chat2:topic2")

    assert store.subscribe(ticket_a["id"], thread_x["id"]) is True
    assert store.subscribe(ticket_a["id"], thread_x["id"]) is True  # idempotent
    assert store.subscribe(ticket_a["id"], thread_y["id"]) is True
    assert store.subscribe(ticket_b["id"], thread_x["id"]) is True
    assert store.subscribe(ticket_b["id"], thread_y["id"], mode="context") is True

    subs_a = store.list_subscriptions(ticket_a["id"])
    assert len(subs_a) == 2
    assert {s["thread_key"] for s in subs_a} == {"tg:chat1:topic1", "tg:chat2:topic2"}

    followers_x = store.thread_subscribers(thread_x["id"])
    assert {s["ticket_id"] for s in followers_x} == {ticket_a["id"], ticket_b["id"]}

    assert store.unsubscribe(ticket_a["id"], thread_x["id"]) is True
    assert store.unsubscribe(ticket_a["id"], thread_x["id"]) is False
    assert len(store.list_subscriptions(ticket_a["id"])) == 1

    store.cancel(ticket_a["id"])
    assert store.list_subscriptions(ticket_a["id"]) == []


def test_fanout_delivers_to_followers_not_context_only(tmp_path):
    store = _store(tmp_path)
    ticket, _ = _submit(store)
    thread = store.ensure_thread("gateway:s1", "tg:chat1:topic1")
    store.subscribe(ticket["id"], thread["id"], mode="follow")

    context_ticket, _ = _submit(store, "event-ctx")
    store.subscribe(context_ticket["id"], thread["id"], mode="context")

    accepted = store.fanout_to_subscribers(thread["id"], "update from thread")
    assert accepted == [ticket["id"]]
    messages = _rows(store, "SELECT ticket_id,text FROM inbox")
    assert messages == [{"ticket_id": ticket["id"], "text": "update from thread"}]


def test_handback_records_and_prevents_double_ownership(tmp_path):
    store = _store(tmp_path)
    ticket, _ = _submit(store)
    trigger_id = _rows(store, "SELECT id FROM triggers")[0]["id"]

    assert store.is_handed_back(trigger_id, ticket["id"]) is False
    store.record_handback(trigger_id, ticket["id"], "not my task")
    assert store.is_handed_back(trigger_id, ticket["id"]) is True
    handbacks = store.list_handbacks(trigger_id)
    assert len(handbacks) == 1
    assert handbacks[0]["reason"] == "not my task"


# --- SA-06: observe mode ---

def test_thread_accept_mode_and_observe_log(tmp_path):
    store = _store(tmp_path)
    thread = store.ensure_thread("gateway:s1", "tg:chat1:topic1")
    assert thread["accept_mode"] == "auto"

    assert store.set_thread_accept_mode(thread["id"], "observe") is True
    assert store.set_thread_accept_mode(thread["id"], "observe") is False
    assert store.get_thread(thread["id"])["accept_mode"] == "observe"

    with pytest.raises(ValueError):
        store.set_thread_accept_mode(thread["id"], "bogus")

    store.record_observe(thread_id=thread["id"], trigger_id=None, action="would_fanout", detail="hello")
    logs = store.list_observe_log()
    assert len(logs) == 1
    assert logs[0]["action"] == "would_fanout"
    assert logs[0]["detail"] == "hello"


# --- SA-07: per-turn metrics ---

def test_turn_metrics_are_recorded_and_retrievable(tmp_path):
    store = _store(tmp_path)
    ticket, _ = _submit(store)
    assignment = store.claim_next()
    turn = store.ensure_initial_turn(assignment["id"], ticket["task"])
    assert store.turn_metrics(turn["id"]) is None

    metrics = {"input_tokens": 1200, "output_tokens": 350, "duration_seconds": 42.5}
    store.record_turn_metrics(turn["id"], metrics)
    assert store.turn_metrics(turn["id"]) == metrics


# --- SA-05: inline/spill context budget ---

def test_spill_content_round_trip(tmp_path):
    store = _store(tmp_path)
    ticket, _ = _submit(store)
    big_content = "项目" * 10000
    ref = store.spill_content(ticket["id"], big_content)
    assert ref.startswith(f"spill/{ticket['id']}/")
    assert store.read_spill(ref) == big_content
    (store.data_dir / ref).write_text(big_content, encoding="utf-8-sig")
    assert store.read_spill(ref) == big_content
    assert store.read_spill("spill/nope/missing.txt") is None
