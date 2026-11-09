"""Tests for the chat-transcript bridge (``hermes_cli.sync_sessions_bridge``).

The bridge carries the Bot's own conversations (state.db) into the KouriChat session
files the device chain syncs (Android PRD v3.0, 同 02 修订) and imports the session
files a negotiation landed back into the Bot's history. Both directions must be
idempotent: a repeated export/import never duplicates rows (merge identity =
role + content + createdAt, matching the Android ``mergeMessages``).
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from hermes_cli.sync_sessions_bridge import export_bot_sessions, import_synced_sessions
from hermes_state import SessionDB

BOT_SOURCE = "cli"


def _seed_bot_session(db: SessionDB, session_id: str, turns: list[tuple[str, str]], started: float) -> None:
    db.import_sessions(
        [
            {
                "id": session_id,
                "source": BOT_SOURCE,
                "title": "seeded",
                "started_at": started,
                "messages": [
                    {"role": role, "content": text, "timestamp": started + offset * 10}
                    for offset, (role, text) in enumerate(turns)
                ],
            }
        ]
    )


def test_export_writes_kourichat_session_files(tmp_path: Path):
    db = SessionDB()
    try:
        _seed_bot_session(db, "20260101_120000_abcd12", [("user", "hello there"), ("assistant", "greetings")], time.time())
    finally:
        db.close()

    exported = export_bot_sessions(tmp_path)
    assert exported == 2

    bridge_state = json.loads((tmp_path / ".meta" / "session-bridge.json").read_text(encoding="utf-8"))
    entry = bridge_state["20260101_120000_abcd12"]
    session_dir = tmp_path / "sessions" / entry["uuid"]
    meta = json.loads((session_dir / "meta.json").read_text(encoding="utf-8"))
    lines = (session_dir / "messages.jsonl").read_text(encoding="utf-8").splitlines()

    assert set(meta) == {"id", "title", "createdAt", "updatedAt", "messageCount"}
    assert meta["messageCount"] == 2
    assert [json.loads(line) for line in lines] == [
        json.loads(line) for line in lines
    ]  # every line is a JSON object
    first = json.loads(lines[0])
    # The Android ChatMessage shape: every kotlinx default serialized.
    assert set(first) == {"role", "content", "createdAt", "image", "images", "memoryTrace"}
    assert first["role"] == "user" and first["image"] is None and first["images"] == []


def test_export_is_incremental(tmp_path: Path):
    session_id = "20260101_130000_abcd12"
    db = SessionDB()
    try:
        _seed_bot_session(db, session_id, [("user", "first")], time.time())
        assert export_bot_sessions(tmp_path) == 1
        db.append_message(session_id, "assistant", "second reply")
        assert export_bot_sessions(tmp_path) == 1  # only the new row
    finally:
        db.close()

    bridge_state = json.loads((tmp_path / ".meta" / "session-bridge.json").read_text(encoding="utf-8"))
    session_dir = tmp_path / "sessions" / bridge_state[session_id]["uuid"]
    lines = (session_dir / "messages.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads((session_dir / "meta.json").read_text(encoding="utf-8"))["messageCount"] == 2


def test_import_lands_sessions_in_bot_history(tmp_path: Path):
    bridge_id = str(uuid.uuid4())
    session_dir = tmp_path / "sessions" / bridge_id
    session_dir.mkdir(parents=True)
    (session_dir / "meta.json").write_text(
        json.dumps({"id": bridge_id, "title": "phone chat", "createdAt": 1000, "updatedAt": 2000, "messageCount": 2}),
        encoding="utf-8",
    )
    (session_dir / "messages.jsonl").write_text(
        json.dumps({"role": "user", "content": "from the phone", "createdAt": 1000, "image": None, "images": [], "memoryTrace": None})
        + "\n"
        + json.dumps({"role": "assistant", "content": "phone reply", "createdAt": 2000, "image": None, "images": [], "memoryTrace": None})
        + "\n",
        encoding="utf-8",
    )

    assert import_synced_sessions(tmp_path) == 2

    db = SessionDB()
    try:
        messages = db.get_messages(bridge_id)
        assert [(m["role"], m["content"]) for m in messages] == [("user", "from the phone"), ("assistant", "phone reply")]
        assert all(abs(m["timestamp"] * 1000 - ms) < 1 for m, ms in zip(messages, (1000, 2000)))
        row = db.get_session(bridge_id)
        assert row["source"] == "kourichat-sync"
        # profile_name is backfilled so the owner ladder can route the session.
        assert row.get("profile_name")
    finally:
        db.close()


def test_import_is_idempotent_and_merges(tmp_path: Path):
    bridge_id = str(uuid.uuid4())
    session_dir = tmp_path / "sessions" / bridge_id
    session_dir.mkdir(parents=True)
    (session_dir / "meta.json").write_text(
        json.dumps({"id": bridge_id, "title": "", "createdAt": 1000, "updatedAt": 2000, "messageCount": 1}),
        encoding="utf-8",
    )
    message = {"role": "user", "content": "hello", "createdAt": 1000, "image": None, "images": [], "memoryTrace": None}
    (session_dir / "messages.jsonl").write_text(json.dumps(message) + "\n", encoding="utf-8")

    assert import_synced_sessions(tmp_path) == 1
    assert import_synced_sessions(tmp_path) == 0  # same file again: nothing to add

    (session_dir / "messages.jsonl").write_text(
        json.dumps(message) + "\n" + json.dumps({"role": "assistant", "content": "welcome", "createdAt": 2000, "image": None, "images": [], "memoryTrace": None}) + "\n",
        encoding="utf-8",
    )
    assert import_synced_sessions(tmp_path) == 1  # only the fresh tail

    db = SessionDB()
    try:
        assert len(db.get_messages(bridge_id)) == 2
    finally:
        db.close()


def test_import_skips_non_bot_and_malformed_rows(tmp_path: Path):
    bridge_id = str(uuid.uuid4())
    session_dir = tmp_path / "sessions" / bridge_id
    session_dir.mkdir(parents=True)
    (session_dir / "meta.json").write_text(
        json.dumps({"id": bridge_id, "title": "", "createdAt": 1, "updatedAt": 3, "messageCount": 3}),
        encoding="utf-8",
    )
    rows = [
        json.dumps({"role": "tool", "content": "internal", "createdAt": 1}),
        json.dumps({"role": "user", "content": "visible", "createdAt": 2000}),
        "{ broken json",
        json.dumps({"role": "user", "content": "", "createdAt": 3000}),  # empty text
    ]
    (session_dir / "messages.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")

    assert import_synced_sessions(tmp_path) == 1
    db = SessionDB()
    try:
        assert [m["content"] for m in db.get_messages(bridge_id)] == ["visible"]
    finally:
        db.close()
