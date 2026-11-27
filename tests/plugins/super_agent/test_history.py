"""History expiry preserves live work, outstanding receipts and event identities."""
import json
import sqlite3
import time

from plugins.super_agent.store import SuperAgentStore


def _count(store, table):
    with sqlite3.connect(store.path) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _drain_outbox(store):
    while (row := store.claim_outbox()) is not None:
        store.settle_outbox(row["id"], delivered=True, attempt=row["attempts"])


def test_pruned_followup_forward_keeps_its_replay_identity(tmp_path, monkeypatch):
    """A redelivered follow-up must not reach the classifier after its ticket expires."""
    from plugins.super_agent import store as storage

    clock = [time.time() - 5 * 86400]
    monkeypatch.setattr(storage, "_now", lambda: clock[0])
    store = SuperAgentStore(tmp_path, "alpha")
    other = SuperAgentStore(tmp_path, "beta")
    conversation = "telegram:demo:"
    source = {"platform": "telegram", "chat_id": "demo", "actor_id": "alice"}
    identity = json.dumps([conversation, "m1"])
    ticket, _ = store.submit(task="original task", sandbox="read-only", workspace_root=str(tmp_path),
                             source_instance=conversation, external_event_id="build-event", source=source)
    assignment = store.claim_next()
    initial = store.ensure_initial_turn(assignment["id"], ticket["task"])
    store.mark_turn_terminal(initial["id"], "initial result")
    assert store.add_inbox(ticket["id"], "追加要求", message_id=identity)
    assert store.add_inbox(ticket["id"], "旧格式追加", message_id="plain-id")
    followup = store.create_followup_turn(assignment["id"])
    store.mark_turn_submitted(followup["id"], "admission-1")
    store.mark_turn_terminal(followup["id"], "follow-up result")
    assert store.complete_if_idle(assignment["id"], "finished")
    _drain_outbox(store)
    assert store.forwarded_count(identity) == 1

    clock[0] += 3 * 86400
    assert store.prune_history(1) == 1
    assert store.get_ticket(ticket["id"]) is None
    assert store.forwarded_count(identity) == 0

    assert store.chat_receipt(conversation, "alice", "m1") == {"id": ticket["id"], "kind": "retired"}
    assert store.chat_receipt(conversation, "alice", "plain-id") == {"id": ticket["id"], "kind": "retired"}
    assert other.chat_receipt(conversation, "alice", "m1") is None
    _, other_created = other.submit(task="other profile", sandbox="read-only", workspace_root=str(tmp_path),
                                    source_instance=conversation, external_event_id="m1", source=source)
    assert other_created

    before = (_count(store, "tickets"), _count(store, "effects"))
    # A classifier that answers NEW would take this exact submit path for the same message.
    replay, created = store.submit(task="追加要求", sandbox="read-only", workspace_root=str(tmp_path),
                                   source_instance=conversation, external_event_id="m1", source=source)
    assert not created and replay["retired"] and replay["id"] == ticket["id"]
    assert (_count(store, "tickets"), _count(store, "effects")) == before


def test_withdrawal_offer_keeps_the_task_after_its_ticket_expires(tmp_path, monkeypatch):
    """Expiry drops the ticket and its trigger text but the pending hand-off keeps the task."""
    from plugins.super_agent import store as storage

    clock = [time.time() - 5 * 86400]
    monkeypatch.setattr(storage, "_now", lambda: clock[0])
    store = SuperAgentStore(tmp_path, "alpha")
    conversation, actor = "telegram:demo:", "alice"
    source = {"platform": "telegram", "chat_id": "demo", "actor_id": actor}
    old_task, new_task = "deploy staging with the release checklist", "rotate the staging credentials"

    def withdrawn(task, event, message):
        ticket, _ = store.submit(task=task, sandbox="read-only", workspace_root=str(tmp_path),
                                 source_instance=conversation, external_event_id=event, source=source)
        assert store.cancel(ticket["id"], withdrawal=(conversation, actor, message))["status"] == "cancelled"
        _drain_outbox(store)
        return ticket

    old = withdrawn(old_task, "build-old", "withdraw-1")
    assert store.chat_offer(conversation, actor) == {"offer_id": "ordinary:withdraw-1", "task": old_task}

    clock[0] += 3 * 86400
    assert store.prune_history(1) == 1
    assert store.get_ticket(old["id"]) is None
    assert (_count(store, "tickets"), _count(store, "triggers")) == (0, 0)
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT profile,source_instance,external_event_id,id,sandbox,status "
                            "FROM retired_tickets").fetchall() == [
            ("alpha", conversation, "build-old", old["id"], "read-only", "cancelled")]
    # Recorded trade-off: no task text survives on the retired ticket, but the unanswered
    # hand-off question still holds the full original task in chat_offers.
    assert store.chat_offer(conversation, actor)["task"] == old_task

    store.offer_handoff(conversation, actor, new_task, "ask-2")
    assert store.chat_offer(conversation, actor) == {"offer_id": "offer:ask-2", "task": new_task}
    assert store.prune_history(1) == 0
    assert store.chat_offer(conversation, actor)["task"] == new_task


def test_prune_keeps_a_ticket_whose_history_write_is_checkpointed(tmp_path, monkeypatch):
    """An acknowledged send whose transcript write is pending keeps its ticket and text."""
    from plugins.super_agent import store as storage

    clock = [time.time() - 5 * 86400]
    monkeypatch.setattr(storage, "_now", lambda: clock[0])
    store = SuperAgentStore(tmp_path, "alpha")
    ticket, _ = store.submit(task="write the report", sandbox="read-only", workspace_root=str(tmp_path),
                             source_instance="telegram:demo:",
                             external_event_id="build-event",
                             source={"platform": "telegram", "chat_id": "demo"})
    assignment = store.claim_next()
    turn = store.ensure_initial_turn(assignment["id"], ticket["task"])
    store.mark_turn_terminal(turn["id"], "the report")
    assert store.complete_if_idle(assignment["id"], "the report")

    claimed = store.claim_outbox()
    assert claimed["history_checkpoint"] is None
    checkpoint = {"identity": "plugin:delivery", "message_id": "m-9"}
    store.settle_outbox(claimed["id"], delivered=False, error="history_pending",
                        attempt=claimed["attempts"], checkpoint=checkpoint)
    with sqlite3.connect(store.path) as conn:
        status, text, stored = conn.execute(
            "SELECT status,text,history_checkpoint FROM outbox WHERE id=?", (claimed["id"],)).fetchone()
    assert status == "pending" and "the report" in text and json.loads(stored) == checkpoint

    clock[0] += 3 * 86400
    assert store.prune_history(1) == 0
    assert store.get_ticket(ticket["id"])["status"] == "succeeded"

    clock[0] += 300
    replay = store.claim_outbox()
    assert (replay["id"], replay["attempts"], replay["text"], replay["history_checkpoint"]) == (
        claimed["id"], 2, claimed["text"], json.dumps(checkpoint))
    store.settle_outbox(replay["id"], delivered=True, attempt=replay["attempts"])
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT status,history_checkpoint FROM outbox WHERE id=?",
                            (claimed["id"],)).fetchone() == ("delivered", None)
    assert store.claim_outbox() is None

    clock[0] += 3 * 86400
    assert store.prune_history(1) == 1
    assert store.get_ticket(ticket["id"]) is None


def test_expiry_keeps_unsettled_other_profiles_and_deduplication(tmp_path, monkeypatch):
    from plugins.super_agent import store as storage

    monkeypatch.setattr(storage, "_now", lambda: 100.0)
    store = SuperAgentStore(tmp_path, "alpha")
    other = SuperAgentStore(tmp_path, "beta")
    source = {"platform": "telegram", "chat_id": "demo"}

    def submit(owner, event, dest):
        return owner.submit(task="private task", sandbox="read-only", workspace_root=str(tmp_path),
                            source_instance="chat", external_event_id=event, source=dest)

    kept, _ = submit(other, "shared-id", None)
    assert other.complete_if_idle(other.claim_next()["id"], "other profile")
    closed, _ = submit(store, "shared-id", None)
    assert store.complete_if_idle(store.claim_next()["id"], "finished")
    pending, _ = submit(store, "pending-delivery", source)
    assert store.complete_if_idle(store.claim_next()["id"], "result for delivery")
    active, _ = submit(store, "active", None)
    assert store.prune_history(0) == 0
    assert store.prune_history(1) == 1
    assert store.get_ticket(closed["id"]) is None
    replay, created = submit(store, "shared-id", None)
    assert not created and replay["id"] == closed["id"] and replay["retired"]
    assert other.get_ticket(kept["id"])["result"] == "other profile"
    assert store.get_ticket(pending["id"])["result"] == "result for delivery"
    assert store.get_ticket(active["id"])["status"] == "queued"


def test_expiry_removes_only_referenced_unchanged_legacy_spills(tmp_path, monkeypatch):
    from plugins.super_agent import store as storage

    monkeypatch.setattr(storage, "_now", lambda: 100.0)
    store = SuperAgentStore(tmp_path, "alpha")
    task = "old task\n" * 1000 + "original\r\nending"
    initial = ("Complete the task in the current Git worktree. Work autonomously and return a concise "
               f"summary, validation evidence, and changed files.\n\nTask:\n{task}")

    def old_ticket(event):
        ticket, _ = store.submit(task=task, sandbox="read-only", workspace_root=str(tmp_path),
                                 source_instance="cli", external_event_id=event, source=None)
        assignment = store.claim_next()
        ref = store.spill_content(ticket["id"], initial)
        stored = initial[:8000] + f"\n\n[Full content spilled to {ref}; read it from the workspace before continuing.]"
        turn = store.ensure_initial_turn(assignment["id"], stored)
        store.mark_turn_terminal(turn["id"], "finished")
        assert store.complete_if_idle(assignment["id"], "finished")
        return ticket["id"], tmp_path / ref

    tid, unix = old_ticket("lf")
    _, windows = old_ticket("crlf")
    _, edited = old_ticket("edited")
    unix.write_bytes(initial.encode("utf-8"))
    windows.write_bytes(initial.replace("\n", "\r\n").encode("utf-8"))
    edited.write_text("operator edit", encoding="utf-8")
    unreferenced = tmp_path / store.spill_content(tid, initial)

    assert store.prune_history(1) == 3
    assert not unix.exists() and not windows.exists()
    assert edited.read_text(encoding="utf-8") == "operator edit"
    assert unreferenced.is_file()