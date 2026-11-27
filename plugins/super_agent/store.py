"""Durable Super Agent tickets, assignments, turns and outbound delivery."""

from __future__ import annotations

from contextlib import contextmanager
import json
import math
from pathlib import Path
import sqlite3
import time
import uuid

from agent.i18n import t

from .workspace import WORKTREE_DIRECTORY


TERMINAL_TICKET_STATES = frozenset({"succeeded", "failed", "cancelled", "taken_over"})
SANDBOX_LEVELS = frozenset({"read-only", "workspace-write", "workspace-exec"})
MAX_RETRIES = 3


def _now() -> float:
    return time.time()


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class SuperAgentStore:
    def __init__(self, data_dir: Path, profile: str):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "super_agent.db"
        self.profile = profile
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @contextmanager
    def _tx(self):
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            yield conn

    def _migrate(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS chat_offers (
                    profile TEXT NOT NULL, conversation TEXT NOT NULL, actor TEXT NOT NULL,
                    offer_id TEXT NOT NULL, task TEXT NOT NULL,
                    PRIMARY KEY(profile, conversation, actor)
                );
                CREATE TABLE IF NOT EXISTS chat_forwards (
                    ticket_id TEXT NOT NULL REFERENCES tickets(id), message_id TEXT NOT NULL,
                    PRIMARY KEY(ticket_id, message_id)
                );
                CREATE TABLE IF NOT EXISTS triggers (
                    id TEXT PRIMARY KEY,
                    profile TEXT NOT NULL,
                    source_instance TEXT NOT NULL,
                    external_event_id TEXT NOT NULL,
                    source_json TEXT,
                    text TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    UNIQUE(profile, source_instance, external_event_id)
                );
                CREATE TABLE IF NOT EXISTS tickets (
                    id TEXT PRIMARY KEY,
                    profile TEXT NOT NULL,
                    trigger_id TEXT NOT NULL REFERENCES triggers(id),
                    task TEXT NOT NULL,
                    status TEXT NOT NULL,
                    sandbox TEXT NOT NULL,
                    workspace_root TEXT NOT NULL,
                    result TEXT,
                    last_error TEXT,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS effects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_id TEXT NOT NULL REFERENCES tickets(id),
                    attempt INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(ticket_id, attempt, kind)
                );
                CREATE TABLE IF NOT EXISTS assignments (
                    id TEXT PRIMARY KEY,
                    ticket_id TEXT NOT NULL REFERENCES tickets(id),
                    effect_id INTEGER NOT NULL UNIQUE REFERENCES effects(id),
                    generation INTEGER NOT NULL,
                    sandbox TEXT NOT NULL,
                    workspace TEXT NOT NULL,
                    worker_session_id TEXT UNIQUE,
                    admission_id TEXT,
                    status TEXT NOT NULL,
                    result TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(ticket_id, generation)
                );
                CREATE TABLE IF NOT EXISTS turns (
                    id TEXT PRIMARY KEY,
                    assignment_id TEXT NOT NULL REFERENCES assignments(id),
                    sequence INTEGER NOT NULL,
                    input_id TEXT NOT NULL UNIQUE,
                    text TEXT NOT NULL,
                    inbox_ids_json TEXT NOT NULL DEFAULT '[]',
                    admission_id TEXT,
                    status TEXT NOT NULL,
                    result TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(assignment_id, sequence)
                );
                CREATE TABLE IF NOT EXISTS inbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_id TEXT NOT NULL REFERENCES tickets(id),
                    text TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS outbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_id TEXT NOT NULL REFERENCES tickets(id),
                    kind TEXT NOT NULL,
                    source_json TEXT NOT NULL,
                    text TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(ticket_id, kind)
                );
                CREATE TABLE IF NOT EXISTS router_state (
                    profile TEXT PRIMARY KEY,
                    paused INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS watchdog_alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile TEXT NOT NULL,
                    assignment_id TEXT NOT NULL REFERENCES assignments(id),
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    suggestion TEXT NOT NULL,
                    first_seen REAL NOT NULL,
                    last_seen REAL NOT NULL,
                    resolved_at REAL,
                    UNIQUE(profile, assignment_id, kind)
                );
                CREATE TABLE IF NOT EXISTS control_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile TEXT NOT NULL,
                    ticket_id TEXT,
                    assignment_id TEXT,
                    action TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS threads (
                    id TEXT PRIMARY KEY,
                    profile TEXT NOT NULL,
                    source_instance TEXT NOT NULL,
                    thread_key TEXT NOT NULL,
                    accept_mode TEXT NOT NULL DEFAULT 'auto',
                    created_at REAL NOT NULL,
                    UNIQUE(profile, source_instance, thread_key)
                );
                CREATE TABLE IF NOT EXISTS subscriptions (
                    ticket_id TEXT NOT NULL REFERENCES tickets(id),
                    thread_id TEXT NOT NULL REFERENCES threads(id),
                    mode TEXT NOT NULL DEFAULT 'follow',
                    created_at REAL NOT NULL,
                    PRIMARY KEY(ticket_id, thread_id)
                );
                CREATE TABLE IF NOT EXISTS trigger_handbacks (
                    trigger_id TEXT NOT NULL REFERENCES triggers(id),
                    ticket_id TEXT NOT NULL REFERENCES tickets(id),
                    reason TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(trigger_id, ticket_id)
                );
                CREATE TABLE IF NOT EXISTS observe_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile TEXT NOT NULL,
                    thread_id TEXT,
                    trigger_id TEXT,
                    action TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(profile, status, created_at);
                CREATE INDEX IF NOT EXISTS idx_effects_status ON effects(status, created_at);
                CREATE INDEX IF NOT EXISTS idx_inbox_ticket ON inbox(ticket_id, status, id);
                CREATE INDEX IF NOT EXISTS idx_outbox_status ON outbox(status, id);
                CREATE INDEX IF NOT EXISTS idx_watchdog_status ON watchdog_alerts(profile, status, last_seen);
                CREATE INDEX IF NOT EXISTS idx_control_events ON control_events(profile, created_at);
                CREATE INDEX IF NOT EXISTS idx_subscriptions_thread ON subscriptions(thread_id);
                CREATE INDEX IF NOT EXISTS idx_observe_log ON observe_log(profile, created_at);
                """
            )
            columns = {row[1] for row in conn.execute("PRAGMA table_info(outbox)")}
            if "kind" not in columns:
                conn.execute("ALTER TABLE outbox ADD COLUMN kind TEXT NOT NULL DEFAULT 'legacy'")
                conn.execute("UPDATE outbox SET kind='legacy:' || id")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_outbox_ticket_kind ON outbox(ticket_id, kind)")
            turn_columns = {row[1] for row in conn.execute("PRAGMA table_info(turns)")}
            if "metrics_json" not in turn_columns:
                conn.execute("ALTER TABLE turns ADD COLUMN metrics_json TEXT")

    def recover(self) -> None:
        """Make only operations with a known retry identity runnable after a crash."""
        now = _now()
        with self._tx() as conn:
            running = conn.execute(
                "SELECT a.id,a.ticket_id,a.worker_session_id FROM assignments a "
                "JOIN tickets t ON t.id=a.ticket_id WHERE t.profile=? AND a.status='running'",
                (self.profile,),
            ).fetchall()
            for assignment in running:
                self._record_event(
                    conn, self.profile, "restart_requeue", "Recovered interrupted Worker after restart.", now,
                    ticket_id=assignment["ticket_id"], assignment_id=assignment["id"],
                )
            conn.execute(
                "UPDATE assignments SET status='interrupted',updated_at=? WHERE id IN ("
                "SELECT a.id FROM assignments a JOIN tickets t ON t.id=a.ticket_id "
                "WHERE t.profile=? AND a.status='running')", (now, self.profile),
            )
            conn.execute(
                "UPDATE turns SET status='pending',updated_at=? WHERE status='running' AND assignment_id IN ("
                "SELECT a.id FROM assignments a JOIN tickets t ON t.id=a.ticket_id WHERE t.profile=?)",
                (now, self.profile),
            )
            conn.execute(
                "UPDATE inbox SET status='pending' WHERE status='delivering' AND ticket_id IN ("
                "SELECT id FROM tickets WHERE profile=?)", (self.profile,),
            )
            conn.execute(
                "UPDATE tickets SET status='queued',updated_at=? WHERE profile=? AND status='running'",
                (now, self.profile),
            )
            conn.execute(
                "UPDATE effects SET status='pending',updated_at=? WHERE status='running' AND ticket_id IN ("
                "SELECT id FROM tickets WHERE profile=?)", (now, self.profile),
            )
            # Delivery may already have reached a platform. Never resend without a platform receipt.
            conn.execute(
                "UPDATE outbox SET status='unknown',updated_at=? WHERE status='sending' AND ticket_id IN ("
                "SELECT id FROM tickets WHERE profile=?)", (now, self.profile),
            )

    def submit(
        self, *, task: str, sandbox: str, workspace_root: str, source_instance: str,
        external_event_id: str, source: dict | None, offer_actor: str | None = None,
    ) -> tuple[dict, bool]:
        task = task.strip()
        if not task:
            raise ValueError("task is required")
        if sandbox not in SANDBOX_LEVELS:
            raise ValueError("sandbox must be read-only, workspace-write, or workspace-exec")
        now, trigger_id, ticket_id = _now(), _id("trigger"), _id("sa")
        source_json = json.dumps(source, ensure_ascii=False, sort_keys=True) if source else None
        with self._tx() as conn:
            existing = conn.execute(
                "SELECT t.* FROM triggers g JOIN tickets t ON t.trigger_id=g.id "
                "WHERE g.profile=? AND g.source_instance=? AND g.external_event_id=?",
                (self.profile, source_instance, external_event_id),
            ).fetchone()
            if offer_actor is not None and not conn.execute(
                "DELETE FROM chat_offers WHERE profile=? AND conversation=? AND actor=? AND offer_id=?",
                (self.profile, source_instance, offer_actor, external_event_id),
            ).rowcount and existing is None:
                raise ValueError(t("super_agent.no_handoff"))
            if existing is not None:
                return dict(existing), False
            if offer_actor is None and source:
                conn.execute("DELETE FROM chat_offers WHERE profile=? AND conversation=? AND actor=?",
                             (self.profile, source_instance, source.get("actor_id", source.get("user_id")) or ""))
            elif offer_actor is None and source_instance.startswith("slash:"):
                conn.execute("DELETE FROM chat_offers WHERE profile=? AND conversation=?", (self.profile, source_instance))
            conn.execute(
                "INSERT INTO triggers VALUES(?,?,?,?,?,?,?)",
                (trigger_id, self.profile, source_instance, external_event_id, source_json, task, now),
            )
            conn.execute(
                "INSERT INTO tickets(id,profile,trigger_id,task,status,sandbox,workspace_root,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (ticket_id, self.profile, trigger_id, task, "queued", sandbox, workspace_root, now, now),
            )
            conn.execute(
                "INSERT INTO effects(ticket_id,attempt,kind,status,created_at,updated_at) "
                "VALUES(?,1,'dispatch','pending',?,?)",
                (ticket_id, now, now),
            )
            return dict(conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()), True

    def get_ticket(self, ticket_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tickets WHERE id=? AND profile=?", (ticket_id, self.profile)).fetchone()
            if row is None:
                return None
            result = dict(row)
            assignment = conn.execute(
                "SELECT * FROM assignments WHERE ticket_id=? ORDER BY generation DESC LIMIT 1", (ticket_id,)
            ).fetchone()
            if assignment is not None:
                result["assignment"] = dict(assignment)
                alert = conn.execute(
                    "SELECT * FROM watchdog_alerts WHERE profile=? AND assignment_id=? AND status='active'",
                    (self.profile, assignment["id"]),
                ).fetchone()
                if alert is not None:
                    result["watchdog_alert"] = dict(alert)
            return result

    def router_state(self) -> dict:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM router_state WHERE profile=?", (self.profile,)).fetchone()
            return {"paused": False, "updated_at": None} if row is None else {
                **dict(row), "paused": bool(row["paused"]),
            }

    def is_paused(self) -> bool:
        return self.router_state()["paused"]

    def set_paused(self, paused: bool) -> bool:
        now = _now()
        with self._tx() as conn:
            row = conn.execute("SELECT paused FROM router_state WHERE profile=?", (self.profile,)).fetchone()
            changed = paused if row is None else bool(row["paused"]) != paused
            conn.execute(
                "INSERT INTO router_state(profile,paused,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(profile) DO UPDATE SET paused=excluded.paused,updated_at=excluded.updated_at",
                (self.profile, int(paused), now),
            )
            if changed:
                self._record_event(
                    conn, self.profile, "pause" if paused else "resume",
                    "Router scheduling and outbound delivery paused." if paused else "Router resumed.", now,
                )
            return changed

    def list_tickets(self, limit: int = 20) -> list[dict]:
        with self._connect() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT * FROM tickets WHERE profile=? ORDER BY created_at DESC LIMIT ?", (self.profile, limit)
            ).fetchall()]

    def chat_tickets(self, conversation: str, actor: str, ticket_id: str = "") -> list[dict]:
        """Routing briefs from durable user input, including tickets created before chat routing."""
        with self._connect() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT t.id,t.status,substr(t.task,1,320) AS goal, "
                "(SELECT substr(i.text,1,200) FROM inbox i WHERE i.ticket_id=t.id "
                " ORDER BY i.id DESC LIMIT 1) AS latest_followup "
                "FROM tickets t JOIN triggers g ON g.id=t.trigger_id WHERE t.profile=? AND g.source_instance=? "
                "AND (g.source_json IS NULL OR COALESCE(json_extract(g.source_json,'$.actor_id'), "
                "json_extract(g.source_json,'$.user_id'),'')=?) "
                "AND ((?='' AND t.status IN ('queued','running')) OR t.id=?) ORDER BY t.created_at DESC",
                (self.profile, conversation, actor, ticket_id, ticket_id),
            )]

    def offer_handoff(self, conversation: str, actor: str, text: str, message_id: str) -> None:
        with self._tx() as conn:
            conn.execute("INSERT OR REPLACE INTO chat_offers VALUES(?,?,?,?,?)",
                         (self.profile, conversation, actor, "offer:" + message_id if message_id else _id("offer"), text))

    def chat_offer(self, conversation: str, actor: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT offer_id,task FROM chat_offers WHERE profile=? AND conversation=? AND actor=?",
                               (self.profile, conversation, actor)).fetchone()
            return dict(row) if row else None

    def clear_offer(self, conversation: str, actor: str, offer_id: str | None = None) -> bool:
        with self._tx() as conn:
            return bool(conn.execute(
                "DELETE FROM chat_offers WHERE profile=? AND conversation=? AND actor=? AND (? IS NULL OR offer_id=?)",
                (self.profile, conversation, actor, offer_id, offer_id),
            ).rowcount)

    def scan_watchdog(self, stale_seconds: float) -> list[dict]:
        """Record alerts for persisted state that has not advanced; never alter execution state."""
        if not math.isfinite(stale_seconds) or stale_seconds <= 0:
            raise ValueError(t("super_agent.watchdog_config"))
        now = _now()
        stale_before = now - stale_seconds
        with self._tx() as conn:
            assignments = conn.execute(
                "SELECT a.id,a.ticket_id,a.worker_session_id,a.workspace,"
                "MAX(a.updated_at,COALESCE(tr.updated_at,a.updated_at)) AS last_progress_at,"
                "tr.metrics_json AS turn_metrics "
                "FROM assignments a JOIN tickets t ON t.id=a.ticket_id "
                "LEFT JOIN turns tr ON tr.assignment_id=a.id AND tr.status='running' "
                "WHERE t.profile=? AND a.status='running' GROUP BY a.id",
                (self.profile,),
            ).fetchall()
            existing = {
                row["assignment_id"]: dict(row) for row in conn.execute(
                    "SELECT * FROM watchdog_alerts WHERE profile=? AND kind='no_state_progress'",
                    (self.profile,),
                ).fetchall()
            }
            stale_ids, new_alerts = set(), []
            for assignment in assignments:
                if assignment["last_progress_at"] > stale_before:
                    continue
                stale_ids.add(assignment["id"])
                age = max(0, int(now - assignment["last_progress_at"]))
                metrics = json.loads(assignment["turn_metrics"] or "{}") if assignment["turn_metrics"] else {}
                tokens_used = (metrics.get("input_tokens") or 0) + (metrics.get("output_tokens") or 0)
                if tokens_used > 0:
                    detail = t("super_agent.stale_tokens", age=age, tokens_used=tokens_used)
                    suggestion = t("super_agent.inspect_loop")
                else:
                    detail = t("super_agent.stale_state", age=age)
                    suggestion = t("super_agent.inspect")
                prior = existing.get(assignment["id"])
                conn.execute(
                    "INSERT INTO watchdog_alerts("
                    "profile,assignment_id,kind,status,detail,suggestion,first_seen,last_seen) "
                    "VALUES(?,?,'no_state_progress','active',?,?,?,?) "
                    "ON CONFLICT(profile,assignment_id,kind) DO UPDATE SET "
                    "status='active',detail=excluded.detail,suggestion=excluded.suggestion,"
                    "last_seen=excluded.last_seen,resolved_at=NULL",
                    (self.profile, assignment["id"], detail, suggestion, now, now),
                )
                if prior is None or prior["status"] != "active":
                    ticket = conn.execute(
                        "SELECT * FROM tickets WHERE id=?", (assignment["ticket_id"],)
                    ).fetchone()
                    self._enqueue_outbox(
                        conn, ticket, f"watchdog:{assignment['id']}:{_id('notice')}",
                        t("super_agent.stale", ticket_id=assignment['ticket_id'], age=age, suggestion=suggestion), now,
                    )
                    new_alerts.append({
                        **dict(assignment), "assignment_id": assignment["id"],
                        "detail": detail, "suggestion": suggestion,
                    })
            for assignment_id, alert in existing.items():
                if alert["status"] == "active" and assignment_id not in stale_ids:
                    conn.execute(
                        "UPDATE watchdog_alerts SET status='resolved',last_seen=?,resolved_at=? WHERE id=?",
                        (now, now, alert["id"]),
                    )
                    conn.execute(
                        "UPDATE outbox SET status='failed',"
                        "error='watchdog alert resolved before delivery',updated_at=? "
                        "WHERE kind LIKE ? AND status='pending'",
                        (now, f"watchdog:{assignment_id}:%"),
                    )
            return new_alerts

    def list_watchdog_alerts(self, *, active_only: bool = True, limit: int = 20) -> list[dict]:
        where = "AND w.status='active'" if active_only else ""
        with self._connect() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT w.*,a.ticket_id,a.worker_session_id,a.workspace FROM watchdog_alerts w "
                "JOIN assignments a ON a.id=w.assignment_id WHERE w.profile=? " + where +
                " ORDER BY w.last_seen DESC LIMIT ?", (self.profile, limit),
            ).fetchall()]

    def list_control_events(self, ticket_id: str | None = None, limit: int = 10) -> list[dict]:
        with self._connect() as conn:
            if ticket_id:
                rows = conn.execute(
                    "SELECT * FROM control_events WHERE profile=? AND ticket_id=? "
                    "ORDER BY created_at DESC LIMIT ?", (self.profile, ticket_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM control_events WHERE profile=? ORDER BY created_at DESC LIMIT ?",
                    (self.profile, limit),
                ).fetchall()
            return [dict(row) for row in rows]

    def claim_next(self) -> dict | None:
        now = _now()
        with self._tx() as conn:
            effect = conn.execute(
                "SELECT e.*,t.task,t.sandbox,t.workspace_root FROM effects e "
                "JOIN tickets t ON t.id=e.ticket_id "
                "WHERE t.profile=? AND t.status='queued' AND e.status='pending' "
                "AND NOT EXISTS(SELECT 1 FROM router_state r WHERE r.profile=? AND r.paused=1) "
                "ORDER BY e.created_at LIMIT 1", (self.profile, self.profile),
            ).fetchone()
            if effect is None:
                return None
            conn.execute("UPDATE effects SET status='running',updated_at=? WHERE id=?", (now, effect["id"]))
            conn.execute("UPDATE tickets SET status='running',updated_at=? WHERE id=?", (now, effect["ticket_id"]))
            assignment = conn.execute("SELECT * FROM assignments WHERE effect_id=?", (effect["id"],)).fetchone()
            if assignment is None:
                generation = conn.execute(
                    "SELECT COALESCE(MAX(generation),0)+1 FROM assignments WHERE ticket_id=?",
                    (effect["ticket_id"],),
                ).fetchone()[0]
                assignment_id = _id("assignment")
                workspace = str(Path(effect["workspace_root"]) / WORKTREE_DIRECTORY / f"{effect['ticket_id']}-g{generation}")
                conn.execute(
                    "INSERT INTO assignments("
                    "id,ticket_id,effect_id,generation,sandbox,workspace,status,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,'running',?,?)",
                    (assignment_id, effect["ticket_id"], effect["id"], generation,
                     effect["sandbox"], workspace, now, now),
                )
                assignment = conn.execute("SELECT * FROM assignments WHERE id=?", (assignment_id,)).fetchone()
            else:
                conn.execute(
                    "UPDATE assignments SET status='running',updated_at=? WHERE id=?",
                    (now, assignment["id"]),
                )
                assignment = conn.execute("SELECT * FROM assignments WHERE id=?", (assignment["id"],)).fetchone()
            return {**dict(effect), **dict(assignment), "effect_id": effect["id"],
                    "task": effect["task"], "workspace_root": effect["workspace_root"]}

    def bind_worker_session(self, assignment_id: str, session_id: str) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE assignments SET worker_session_id=?,updated_at=? WHERE id=? AND status='running'",
                (session_id, _now(), assignment_id),
            )

    def ensure_initial_turn(self, assignment_id: str, text: str) -> dict:
        now = _now()
        with self._tx() as conn:
            existing = conn.execute(
                "SELECT * FROM turns WHERE assignment_id=? ORDER BY sequence LIMIT 1", (assignment_id,)
            ).fetchone()
            if existing is None:
                assignment = conn.execute(
                    "SELECT ticket_id,generation FROM assignments WHERE id=?", (assignment_id,)
                ).fetchone()
                turn_id = _id("turn")
                input_id = f"super-agent:{assignment['ticket_id']}:{assignment['generation']}:1"
                conn.execute(
                    "INSERT INTO turns(id,assignment_id,sequence,input_id,text,status,created_at,updated_at) "
                    "VALUES(?,?,1,?,?,'pending',?,?)",
                    (turn_id, assignment_id, input_id, text, now, now),
                )
                existing = conn.execute("SELECT * FROM turns WHERE id=?", (turn_id,)).fetchone()
            return dict(existing)

    def pending_turn(self, assignment_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM turns WHERE assignment_id=? "
                "AND status IN ('pending','running') ORDER BY sequence LIMIT 1",
                (assignment_id,),
            ).fetchone()
            return dict(row) if row is not None else None

    def create_followup_turn(self, assignment_id: str) -> dict | None:
        now = _now()
        with self._tx() as conn:
            assignment = conn.execute(
                "SELECT ticket_id,generation,status FROM assignments WHERE id=?", (assignment_id,)
            ).fetchone()
            if assignment is None or assignment["status"] != "running":
                return None
            active = conn.execute(
                "SELECT * FROM turns WHERE assignment_id=? "
                "AND status IN ('pending','running') ORDER BY sequence LIMIT 1",
                (assignment_id,),
            ).fetchone()
            if active is not None:
                return dict(active)
            messages = conn.execute(
                "SELECT id,text FROM inbox WHERE ticket_id=? AND status='pending' ORDER BY id",
                (assignment["ticket_id"],),
            ).fetchall()
            if not messages:
                return None
            ids = [row["id"] for row in messages]
            placeholders = ",".join("?" for _ in ids)
            conn.execute(f"UPDATE inbox SET status='delivering' WHERE id IN ({placeholders})", ids)
            sequence = conn.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 FROM turns WHERE assignment_id=?", (assignment_id,)
            ).fetchone()[0]
            turn_id = _id("turn")
            input_id = f"super-agent:{assignment['ticket_id']}:{assignment['generation']}:{sequence}"
            text = "User follow-up:\n" + "\n".join(f"- {row['text']}" for row in messages)
            conn.execute(
                "INSERT INTO turns("
                "id,assignment_id,sequence,input_id,text,inbox_ids_json,status,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,'pending',?,?)",
                (turn_id, assignment_id, sequence, input_id, text, json.dumps(ids), now, now),
            )
            return dict(conn.execute("SELECT * FROM turns WHERE id=?", (turn_id,)).fetchone())

    def mark_turn_submitted(self, turn_id: str, admission_id: str) -> None:
        now = _now()
        with self._tx() as conn:
            conn.execute("UPDATE turns SET admission_id=?,status='running',updated_at=? WHERE id=?",
                         (admission_id, now, turn_id))
            conn.execute(
                "UPDATE assignments SET admission_id=?,updated_at=? "
                "WHERE id=(SELECT assignment_id FROM turns WHERE id=?)",
                (admission_id, now, turn_id),
            )

    def mark_turn_terminal(self, turn_id: str, result: str) -> None:
        now = _now()
        with self._tx() as conn:
            row = conn.execute("SELECT inbox_ids_json,status FROM turns WHERE id=?", (turn_id,)).fetchone()
            if row is None or row["status"] == "terminal":
                return
            conn.execute("UPDATE turns SET status='terminal',result=?,updated_at=? WHERE id=?", (result, now, turn_id))
            ids = json.loads(row["inbox_ids_json"] or "[]")
            if ids:
                conn.execute(
                    f"UPDATE inbox SET status='consumed' WHERE id IN ({','.join('?' for _ in ids)})", ids
                )

    def add_inbox(self, ticket_id: str, text: str, *, message_id: str = "") -> bool:
        text = text.strip()
        if not text:
            raise ValueError("message is required")
        with self._tx() as conn:
            ticket = conn.execute(
                "SELECT status FROM tickets WHERE id=? AND profile=?", (ticket_id, self.profile)
            ).fetchone()
            if ticket is None or ticket["status"] in TERMINAL_TICKET_STATES:
                return False
            self._append_inbox(conn, ticket_id, text, message_id)
            return True

    @staticmethod
    def _append_inbox(conn, ticket_id: str, text: str, message_id: str) -> None:
        if message_id and not conn.execute(
            "INSERT OR IGNORE INTO chat_forwards VALUES(?,?)", (ticket_id, message_id)
        ).rowcount:
            return
        conn.execute("INSERT INTO inbox(ticket_id,text,status,created_at) VALUES(?,?,'pending',?)",
                     (ticket_id, text, _now()))

    def complete_if_idle(self, assignment_id: str, result: str, *, home_platforms=()) -> bool:
        now = _now()
        with self._tx() as conn:
            assignment = conn.execute("SELECT * FROM assignments WHERE id=?", (assignment_id,)).fetchone()
            if assignment is None or assignment["status"] != "running":
                return False
            latest = conn.execute(
                "SELECT id,generation FROM assignments WHERE ticket_id=? ORDER BY generation DESC LIMIT 1",
                (assignment["ticket_id"],),
            ).fetchone()
            ticket = conn.execute("SELECT * FROM tickets WHERE id=?", (assignment["ticket_id"],)).fetchone()
            if latest["id"] != assignment_id or ticket["status"] != "running":
                return False
            pending = conn.execute(
                "SELECT 1 FROM inbox WHERE ticket_id=? AND status IN ('pending','delivering') LIMIT 1",
                (assignment["ticket_id"],),
            ).fetchone()
            if pending is not None:
                return False
            conn.execute("UPDATE assignments SET status='succeeded',result=?,updated_at=? WHERE id=?",
                         (result, now, assignment_id))
            conn.execute("UPDATE effects SET status='done',updated_at=? WHERE id=?", (now, assignment["effect_id"]))
            conn.execute("UPDATE tickets SET status='succeeded',result=?,updated_at=? WHERE id=?",
                         (result, now, assignment["ticket_id"]))
            conn.execute("DELETE FROM subscriptions WHERE ticket_id=?", (assignment["ticket_id"],))
            self._enqueue_outbox(
                conn, ticket, f"assignment:{assignment_id}:succeeded",
                t("super_agent.completed", id=ticket['id'], result=result), now, home_platforms=home_platforms,
            )
            return True

    def fail_assignment(self, assignment_id: str, error: str) -> bool:
        now, error = _now(), str(error)[:4000]
        with self._tx() as conn:
            assignment = conn.execute("SELECT * FROM assignments WHERE id=?", (assignment_id,)).fetchone()
            if assignment is None or assignment["status"] not in {"running", "interrupted"}:
                return False
            latest = conn.execute(
                "SELECT id FROM assignments WHERE ticket_id=? ORDER BY generation DESC LIMIT 1",
                (assignment["ticket_id"],),
            ).fetchone()
            ticket = conn.execute("SELECT * FROM tickets WHERE id=?", (assignment["ticket_id"],)).fetchone()
            if latest["id"] != assignment_id or ticket["status"] != "running":
                return False
            conn.execute("UPDATE assignments SET status='failed',result=?,updated_at=? WHERE id=?",
                         (error, now, assignment_id))
            conn.execute("UPDATE effects SET status='done',updated_at=? WHERE id=?", (now, assignment["effect_id"]))
            conn.execute("UPDATE tickets SET status='failed',last_error=?,updated_at=? WHERE id=?",
                         (error, now, assignment["ticket_id"]))
            self._enqueue_outbox(
                conn, ticket, f"assignment:{assignment_id}:failed",
                t("super_agent.failed", id=ticket['id'], error=error), now,
            )
            return True

    @staticmethod
    def _enqueue_outbox(conn, ticket, kind: str, text: str, now: float, *, home_platforms=()) -> None:
        source = conn.execute(
            "SELECT source_json FROM triggers WHERE id=?", (ticket["trigger_id"],)
        ).fetchone()["source_json"]
        targets = [(kind, source)] if source else []
        targets.extend((f"{kind}:home:{platform}", json.dumps({"home_channel": platform})) for platform in home_platforms)
        for delivery_kind, destination in targets:
            conn.execute(
                "INSERT OR IGNORE INTO outbox(ticket_id,kind,source_json,text,status,created_at,updated_at) "
                "VALUES(?,?,?,?, 'pending',?,?)", (ticket["id"], delivery_kind, destination, text, now, now),
            )

    @staticmethod
    def _record_event(
        conn, profile: str, action: str, detail: str, now: float, *,
        ticket_id: str | None = None, assignment_id: str | None = None,
    ) -> None:
        conn.execute(
            "INSERT INTO control_events(profile,ticket_id,assignment_id,action,detail,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (profile, ticket_id, assignment_id, action, detail, now),
        )

    def cancel(self, ticket_id: str) -> dict | None:
        now = _now()
        with self._tx() as conn:
            ticket = conn.execute(
                "SELECT * FROM tickets WHERE id=? AND profile=?", (ticket_id, self.profile)
            ).fetchone()
            if ticket is None:
                return None
            if ticket["status"] in TERMINAL_TICKET_STATES:
                return dict(ticket) if ticket["status"] == "cancelled" else None
            conn.execute("UPDATE tickets SET status='cancelled',updated_at=? WHERE id=?", (now, ticket_id))
            conn.execute("UPDATE effects SET status='done',updated_at=? WHERE ticket_id=? AND status!='done'",
                         (now, ticket_id))
            conn.execute(
                "UPDATE assignments SET status='cancelled',updated_at=? "
                "WHERE ticket_id=? AND status!='succeeded'", (now, ticket_id),
            )
            conn.execute("DELETE FROM subscriptions WHERE ticket_id=?", (ticket_id,))
            self._record_event(
                conn, self.profile, "cancel", "Ticket stopped by an operator.", now,
                ticket_id=ticket_id,
            )
            self._enqueue_outbox(
                conn, ticket, "cancelled", t("super_agent.cancelled", ticket_id=ticket['id']), now,
            )
            current = conn.execute(
                "SELECT a.*,tr.admission_id AS turn_admission_id FROM assignments a "
                "LEFT JOIN turns tr ON tr.assignment_id=a.id AND tr.status='running' "
                "WHERE a.ticket_id=? ORDER BY a.generation DESC LIMIT 1", (ticket_id,),
            ).fetchone()
            return dict(current) if current is not None else {**dict(ticket), "status": "cancelled"}

    def takeover(self, ticket_id: str) -> dict | None:
        now = _now()
        with self._tx() as conn:
            ticket = conn.execute(
                "SELECT * FROM tickets WHERE id=? AND profile=?", (ticket_id, self.profile)
            ).fetchone()
            if ticket is None or ticket["status"] in TERMINAL_TICKET_STATES:
                return None
            current = conn.execute(
                "SELECT a.*,tr.admission_id AS turn_admission_id FROM assignments a "
                "LEFT JOIN turns tr ON tr.assignment_id=a.id AND tr.status='running' "
                "WHERE a.ticket_id=? ORDER BY a.generation DESC LIMIT 1", (ticket_id,),
            ).fetchone()
            conn.execute("UPDATE tickets SET status='taken_over',updated_at=? WHERE id=?", (now, ticket_id))
            conn.execute(
                "UPDATE effects SET status='done',updated_at=? WHERE ticket_id=? AND status!='done'",
                (now, ticket_id),
            )
            conn.execute(
                "UPDATE assignments SET status='taken_over',updated_at=? WHERE ticket_id=? "
                "AND status IN ('running','interrupted')", (now, ticket_id),
            )
            conn.execute("DELETE FROM subscriptions WHERE ticket_id=?", (ticket_id,))
            self._record_event(
                conn, self.profile, "takeover",
                "Automation stopped; Worker Session and worktree retained for operator review.", now,
                ticket_id=ticket_id, assignment_id=current["id"] if current is not None else None,
            )
            self._enqueue_outbox(
                conn, ticket, "taken_over",
                t("super_agent.taken_over", id=ticket['id']),
                now,
            )
            return dict(current) if current is not None else {**dict(ticket), "status": "taken_over"}

    def retry(self, ticket_id: str) -> bool:
        now = _now()
        with self._tx() as conn:
            ticket = conn.execute(
                "SELECT * FROM tickets WHERE id=? AND profile=?", (ticket_id, self.profile)
            ).fetchone()
            if (
                ticket is None
                or ticket["status"] not in {"failed", "cancelled", "taken_over"}
                or ticket["retry_count"] >= MAX_RETRIES
            ):
                return False
            attempt = conn.execute(
                "SELECT COALESCE(MAX(attempt),0)+1 FROM effects WHERE ticket_id=?", (ticket_id,)
            ).fetchone()[0]
            conn.execute(
                "UPDATE tickets SET status='queued',result=NULL,last_error=NULL,"
                "retry_count=retry_count+1,updated_at=? "
                "WHERE id=?", (now, ticket_id),
            )
            conn.execute(
                "INSERT INTO effects(ticket_id,attempt,kind,status,created_at,updated_at) "
                "VALUES(?,?,'dispatch','pending',?,?)", (ticket_id, attempt, now, now),
            )
            self._record_event(
                conn, self.profile, "retry",
                f"Operator queued retry {ticket['retry_count'] + 1} of {MAX_RETRIES}.", now,
                ticket_id=ticket_id,
            )
            return True

    def assignment_policy(self, session_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id,ticket_id,sandbox,status FROM assignments WHERE worker_session_id=?", (session_id,)
            ).fetchone()
            return dict(row) if row is not None else None

    def assignment_sandbox(self, session_id: str) -> str | None:
        policy = self.assignment_policy(session_id)
        return policy["sandbox"] if policy is not None and policy["status"] == "running" else None

    def claim_outbox(self) -> dict | None:
        with self._tx() as conn:
            row = conn.execute(
                "SELECT o.* FROM outbox o JOIN tickets t ON t.id=o.ticket_id "
                "WHERE t.profile=? AND o.status='pending' "
                "AND NOT EXISTS(SELECT 1 FROM router_state r WHERE r.profile=? AND r.paused=1) "
                "ORDER BY o.id LIMIT 1", (self.profile, self.profile),
            ).fetchone()
            if row is None:
                return None
            conn.execute("UPDATE outbox SET status='sending',updated_at=? WHERE id=?", (_now(), row["id"]))
            return dict(row)

    def settle_outbox(self, outbox_id: int, *, delivered: bool, error: str = "") -> None:
        status = "delivered" if delivered else "unknown" if error == "timeout" else "failed"
        with self._tx() as conn:
            conn.execute("UPDATE outbox SET status=?,error=?,updated_at=? WHERE id=?",
                         (status, str(error)[:1000] or None, _now(), outbox_id))

    # --- Threads and subscriptions (SA-05 multi-to-many) ---

    def ensure_thread(self, source_instance: str, thread_key: str, accept_mode: str = "auto") -> dict:
        now = _now()
        with self._tx() as conn:
            row = conn.execute(
                "SELECT * FROM threads WHERE profile=? AND source_instance=? AND thread_key=?",
                (self.profile, source_instance, thread_key),
            ).fetchone()
            if row is not None:
                return dict(row)
            thread_id = _id("thread")
            conn.execute(
                "INSERT INTO threads(id,profile,source_instance,thread_key,accept_mode,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (thread_id, self.profile, source_instance, thread_key, accept_mode, now),
            )
            return dict(conn.execute("SELECT * FROM threads WHERE id=?", (thread_id,)).fetchone())

    def set_thread_accept_mode(self, thread_id: str, mode: str) -> bool:
        if mode not in ("off", "observe", "auto"):
            raise ValueError("accept_mode must be off, observe, or auto")
        with self._tx() as conn:
            row = conn.execute(
                "SELECT accept_mode FROM threads WHERE id=? AND profile=?", (thread_id, self.profile)
            ).fetchone()
            if row is None:
                return False
            if row["accept_mode"] == mode:
                return False
            conn.execute("UPDATE threads SET accept_mode=? WHERE id=?", (mode, thread_id))
            return True

    def get_thread(self, thread_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM threads WHERE id=? AND profile=?", (thread_id, self.profile)
            ).fetchone()
            return dict(row) if row is not None else None

    def find_thread(self, source_instance: str, thread_key: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM threads WHERE profile=? AND source_instance=? AND thread_key=?",
                (self.profile, source_instance, thread_key),
            ).fetchone()
            return dict(row) if row is not None else None

    def subscribe(self, ticket_id: str, thread_id: str, mode: str = "follow") -> bool:
        if mode not in ("follow", "context"):
            raise ValueError("subscription mode must be follow or context")
        with self._tx() as conn:
            ticket = conn.execute(
                "SELECT status FROM tickets WHERE id=? AND profile=?", (ticket_id, self.profile)
            ).fetchone()
            if ticket is None or ticket["status"] in TERMINAL_TICKET_STATES:
                return False
            conn.execute(
                "INSERT OR IGNORE INTO subscriptions(ticket_id,thread_id,mode,created_at) VALUES(?,?,?,?)",
                (ticket_id, thread_id, mode, _now()),
            )
            return True

    def unsubscribe(self, ticket_id: str, thread_id: str) -> bool:
        with self._tx() as conn:
            cursor = conn.execute(
                "DELETE FROM subscriptions WHERE ticket_id=? AND thread_id=?", (ticket_id, thread_id)
            )
            return cursor.rowcount > 0

    def list_subscriptions(self, ticket_id: str) -> list[dict]:
        with self._connect() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT s.*,t.thread_key,t.source_instance,t.accept_mode FROM subscriptions s "
                "JOIN threads t ON t.id=s.thread_id WHERE s.ticket_id=? ORDER BY s.created_at",
                (ticket_id,),
            ).fetchall()]

    def thread_subscribers(self, thread_id: str) -> list[dict]:
        with self._connect() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT s.ticket_id,s.mode,t.status,t.task FROM subscriptions s "
                "JOIN tickets t ON t.id=s.ticket_id WHERE s.thread_id=? "
                "AND t.status NOT IN ('succeeded','failed','cancelled','taken_over')",
                (thread_id,),
            ).fetchall()]

    def cleanup_subscriptions(self, ticket_id: str) -> None:
        with self._tx() as conn:
            conn.execute("DELETE FROM subscriptions WHERE ticket_id=?", (ticket_id,))

    # --- Trigger handback (SA-05) ---

    def record_handback(self, trigger_id: str, ticket_id: str, reason: str) -> bool:
        with self._tx() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO trigger_handbacks(trigger_id,ticket_id,reason,created_at) VALUES(?,?,?,?)",
                (trigger_id, ticket_id, reason[:1000], _now()),
            )
            return True

    def is_handed_back(self, trigger_id: str, ticket_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM trigger_handbacks WHERE trigger_id=? AND ticket_id=?",
                (trigger_id, ticket_id),
            ).fetchone()
            return row is not None

    def list_handbacks(self, trigger_id: str) -> list[dict]:
        with self._connect() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT * FROM trigger_handbacks WHERE trigger_id=? ORDER BY created_at", (trigger_id,)
            ).fetchall()]

    # --- Observe mode (SA-06) ---

    def record_observe(self, *, thread_id: str | None, trigger_id: str | None, action: str, detail: str) -> None:
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO observe_log(profile,thread_id,trigger_id,action,detail,created_at) VALUES(?,?,?,?,?,?)",
                (self.profile, thread_id, trigger_id, action, detail[:2000], _now()),
            )

    def list_observe_log(self, limit: int = 20) -> list[dict]:
        with self._connect() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT * FROM observe_log WHERE profile=? ORDER BY created_at DESC LIMIT ?",
                (self.profile, limit),
            ).fetchall()]

    # --- Fan-out delivery to subscribed tickets (SA-05) ---

    def fanout_to_subscribers(self, thread_id: str, text: str, message_id: str = "") -> list[str]:
        """Deliver a message to every live subscriber of a thread; returns accepted ticket IDs."""
        accepted: list[str] = []
        with self._tx() as conn:
            subscribers = conn.execute(
                "SELECT s.ticket_id,s.mode FROM subscriptions s "
                "JOIN tickets t ON t.id=s.ticket_id WHERE s.thread_id=? "
                "AND t.status NOT IN ('succeeded','failed','cancelled','taken_over')",
                (thread_id,),
            ).fetchall()
            for sub in subscribers:
                if sub["mode"] == "context":
                    continue
                accepted.append(sub["ticket_id"])
                self._append_inbox(conn, sub["ticket_id"], text, message_id)
        return accepted

    # --- Per-turn metrics (SA-07) ---

    def record_turn_metrics(self, turn_id: str, metrics: dict) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE turns SET metrics_json=? WHERE id=?",
                (json.dumps(metrics, ensure_ascii=False, sort_keys=True), turn_id),
            )

    def turn_metrics(self, turn_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT metrics_json FROM turns WHERE id=?", (turn_id,)).fetchone()
            if row is None or not row["metrics_json"]:
                return None
            return json.loads(row["metrics_json"])

    # --- Inline/Spill context budget (SA-05) ---

    def spill_content(self, ticket_id: str, content: str) -> str:
        """Write oversized content to a spill file; returns a relative reference."""
        spill_dir = self.data_dir / "spill" / ticket_id
        spill_dir.mkdir(parents=True, exist_ok=True)
        name = f"{uuid.uuid4().hex[:12]}.txt"
        (spill_dir / name).write_text(content, encoding="utf-8")
        return f"spill/{ticket_id}/{name}"

    def read_spill(self, ref: str) -> str | None:
        path = self.data_dir / ref
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8-sig")

    def counts(self) -> dict[str, int]:
        with self._connect() as conn:
            return {row["status"]: row["n"] for row in conn.execute(
                "SELECT status,COUNT(*) AS n FROM tickets WHERE profile=? GROUP BY status", (self.profile,)
            ).fetchall()}
