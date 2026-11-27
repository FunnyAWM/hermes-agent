"""Profile-scoped ticket inspection and expiry of settled history."""
from __future__ import annotations

import json
from pathlib import Path
import time

from .worker_input import initial_prompt, remove_legacy_spill, remove_owned_input


def _forward_identity(message_id: str, fallback: str) -> tuple[str, str]:
    """Recover the conversation and raw message ID chat.py packs into a forward key."""
    try:
        value = json.loads(message_id)
    except (TypeError, ValueError):
        return fallback, message_id
    if isinstance(value, list) and len(value) == 2 and all(isinstance(part, str) and part for part in value):
        return value[0], value[1]
    return fallback, message_id


def details(conn, profile: str, ticket_id: str) -> dict:
    turns = conn.execute(
        "SELECT a.generation,tr.sequence,tr.result,tr.metrics_json FROM turns tr "
        "JOIN assignments a ON a.id=tr.assignment_id JOIN tickets t ON t.id=a.ticket_id "
        "WHERE t.profile=? AND t.id=? ORDER BY a.generation,tr.sequence", (profile, ticket_id),
    )
    handbacks = conn.execute(
        "SELECT h.trigger_id,h.reason FROM trigger_handbacks h JOIN tickets t ON t.id=h.ticket_id "
        "WHERE t.profile=? AND t.id=? ORDER BY h.created_at", (profile, ticket_id),
    )
    return {
        "turns": [{"generation": row["generation"], "sequence": row["sequence"], "result": row["result"],
                   "metrics": json.loads(row["metrics_json"] or "{}")} for row in turns],
        "handbacks": [dict(row) for row in handbacks],
    }


def prune(conn, profile: str, days: int, data_dir: Path) -> int:
    if isinstance(days, bool) or not isinstance(days, (int, float)) or days < 0 or not float(days).is_integer():
        raise ValueError("history_retention_days must be a non-negative integer")
    if not days:
        return 0
    cutoff = time.time() - days * 86400
    tickets = conn.execute(
        "SELECT t.id,t.trigger_id,t.task,t.sandbox,t.status,g.source_instance,g.external_event_id FROM tickets t "
        "JOIN triggers g ON g.id=t.trigger_id WHERE t.profile=? AND t.updated_at<? "
        "AND t.status IN ('succeeded','failed','cancelled','taken_over') "
        "AND NOT EXISTS (SELECT 1 FROM outbox o WHERE o.ticket_id=t.id "
        "AND o.status IN ('pending','sending','unknown')) "
        "AND NOT EXISTS (SELECT 1 FROM turns tr JOIN assignments a ON a.id=tr.assignment_id "
        "WHERE a.ticket_id=t.id AND tr.stop_confirmed=0 AND a.worker_session_id IS NOT NULL "
        "AND tr.status IN ('running','pending') AND (tr.admission_id IS NOT NULL OR tr.started_at IS NOT NULL) "
        "AND (tr.stop_reason IS NOT NULL OR a.status IN ('cancelled','taken_over'))) "
        "ORDER BY t.updated_at LIMIT 50", (profile, cutoff),
    ).fetchall()
    for ticket in tickets:
        tid, trigger = ticket["id"], ticket["trigger_id"]
        for row in conn.execute(
            "SELECT w.*,a.workspace FROM worker_inputs w JOIN turns tr ON tr.id=w.turn_id "
            "JOIN assignments a ON a.id=tr.assignment_id WHERE a.ticket_id=?", (tid,),
        ):
            remove_owned_input(Path(row["workspace"]), row)
        for row in conn.execute(
            "SELECT tr.text FROM turns tr JOIN assignments a ON a.id=tr.assignment_id "
            "WHERE a.ticket_id=? AND tr.sequence=1 AND tr.text LIKE '%[Full content spilled to spill/%'", (tid,),
        ):
            remove_legacy_spill(data_dir, row["text"], initial_prompt(ticket["task"]), tid)
        conn.execute("INSERT INTO retired_tickets VALUES(?,?,?,?,?,?)",
                     (profile, ticket["source_instance"], ticket["external_event_id"], tid,
                      ticket["sandbox"], ticket["status"]))
        for forward in conn.execute("SELECT message_id FROM chat_forwards WHERE ticket_id=?", (tid,)):
            conversation, raw_id = _forward_identity(forward["message_id"], ticket["source_instance"])
            conn.execute("INSERT OR IGNORE INTO retired_tickets VALUES(?,?,?,?,?,?)",
                         (profile, conversation, raw_id, tid, ticket["sandbox"], ticket["status"]))
        conn.execute("DELETE FROM trigger_handbacks WHERE ticket_id=? OR trigger_id=?", (tid, trigger))
        conn.execute("DELETE FROM watchdog_alerts WHERE assignment_id IN (SELECT id FROM assignments WHERE ticket_id=?)",
                     (tid,))
        conn.execute("DELETE FROM turns WHERE assignment_id IN (SELECT id FROM assignments WHERE ticket_id=?)", (tid,))
        for table in ("chat_forwards", "subscriptions", "outbox", "inbox", "control_events", "assignments", "effects"):
            conn.execute(f"DELETE FROM {table} WHERE ticket_id=?", (tid,))
        conn.execute("DELETE FROM tickets WHERE id=?", (tid,))
        conn.execute("DELETE FROM triggers WHERE id=?", (trigger,))
    conn.execute("DELETE FROM observe_log WHERE id IN (SELECT id FROM observe_log WHERE profile=? AND created_at<? "
                 "AND (trigger_id IS NULL OR NOT EXISTS (SELECT 1 FROM tickets WHERE trigger_id=observe_log.trigger_id)) "
                 "ORDER BY id LIMIT 500)", (profile, cutoff))
    conn.execute("DELETE FROM control_events WHERE id IN (SELECT id FROM control_events "
                 "WHERE profile=? AND ticket_id IS NULL AND created_at<? LIMIT 500)", (profile, cutoff))
    return len(tickets)