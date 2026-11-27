"""SQLite operations for durable, destination-scoped notifications."""
from __future__ import annotations

import json

SEND_TIMEOUT_SECONDS = 30
# A second dispatcher may reclaim a send after its network deadline and SQLite's write timeout.
CLAIM_TTL_SECONDS = 120


def migrate(conn, columns):
    if "history_checkpoint" not in columns:
        conn.execute("ALTER TABLE outbox ADD COLUMN history_checkpoint TEXT")
    if "attempts" not in columns:
        conn.execute("ALTER TABLE outbox ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")
        conn.execute("ALTER TABLE outbox ADD COLUMN next_attempt_at REAL NOT NULL DEFAULT 0")
        conn.execute("ALTER TABLE outbox ADD COLUMN receipt TEXT")
        # An old in-flight send has no stable ID at its recipient. Leave replay to the operator.
        conn.execute("UPDATE outbox SET status='unknown',error='legacy_unconfirmed' WHERE status='sending'")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_outbox_due ON outbox(status,next_attempt_at,id)")


def enqueue(conn, ticket, kind, text, now, home_platforms):
    source = conn.execute("SELECT source_json FROM triggers WHERE id=?", (ticket["trigger_id"],)).fetchone()[0]
    targets = [(kind, source)] if source else []
    if home_platforms is None:
        targets.append((kind + ":homes", '{"discover_homes":true}'))
    else:
        targets.extend((kind + ":home:" + platform, json.dumps({"home_channel": platform})) for platform in home_platforms)
    conn.executemany(
        "INSERT OR IGNORE INTO outbox(ticket_id,kind,source_json,text,status,created_at,updated_at) "
        "VALUES(?,?,?,?,'pending',?,?)",
        [(ticket["id"], key, destination, text, now, now) for key, destination in targets],
    )


def ticket_deliveries(conn, ticket_id):
    records = []
    for row in conn.execute(
        "SELECT id,kind,source_json,status,attempts,error,next_attempt_at,receipt FROM outbox "
        "WHERE ticket_id=? AND status!='expanded' ORDER BY id DESC", (ticket_id,),
    ):
        record = dict(row)
        source = json.loads(record.pop("source_json"))
        record["target"] = source.get("home_channel") or source.get("platform") or "home"
        records.append(record)
    return records


def claim(conn, profile, now):
    return conn.execute(
        "UPDATE outbox SET status='sending',updated_at=?,attempts=attempts+1 WHERE id=("
        "SELECT o.id FROM outbox o JOIN tickets t ON t.id=o.ticket_id WHERE t.profile=? "
        "AND ((o.status='pending' AND o.next_attempt_at<=?) OR (o.status='sending' AND o.updated_at<=?)) "
        "AND NOT EXISTS(SELECT 1 FROM router_state WHERE profile=? AND paused=1) "
        "AND NOT EXISTS(SELECT 1 FROM outbox earlier WHERE earlier.ticket_id=o.ticket_id "
        "AND earlier.source_json=o.source_json AND earlier.id<o.id AND earlier.status IN ('pending','sending')) "
        "ORDER BY o.next_attempt_at,o.id LIMIT 1) RETURNING *", (now, profile, now, now - CLAIM_TTL_SECONDS, profile),
    ).fetchone()


def settle(conn, profile, row_id, *, delivered, error, receipt, attempt, now, checkpoint=None):
    if delivered:
        status = "delivered"
    elif error in {"permission_denied", "source_disabled", "invalid_source", "no_conversation"}:
        status = "failed"
    else:
        status = "pending"
    conn.execute(
        "UPDATE outbox SET status=?,error=?,receipt=COALESCE(?,receipt),updated_at=?,"
        "history_checkpoint=CASE WHEN ? THEN NULL ELSE COALESCE(?,history_checkpoint) END,next_attempt_at="
        "CASE WHEN ?='pending' THEN ? + MIN(300,(1 << MIN(attempts,9))) ELSE 0 END "
        "WHERE id=? AND status='sending' AND (? IS NULL OR attempts=?) "
        "AND ticket_id IN (SELECT id FROM tickets WHERE profile=?)",
        (status, str(error)[:1000] or None, receipt, now, delivered,
         json.dumps(checkpoint) if checkpoint else None, status, now, row_id, attempt, attempt, profile),
    )
