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
from persistence.conversation.messages import NewMessage
from persistence_assembly import open_persistence, profile_scope

BOT_SOURCE = "cli"


def _seed_bot_session(db, session_id: str, turns: list[tuple[str, str]], started: float) -> None:
    db.maintenance.import_sessions(
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


def _write_phone_session(root: Path, name: str, rows: list[tuple[str, str, int]]) -> None:
    """A session as the phone's SessionStore would have written it."""
    directory = root / "sessions" / name
    directory.mkdir(parents=True, exist_ok=True)
    meta = {
        "id": name,
        "title": "phone chat",
        "createdAt": rows[0][2],
        "updatedAt": rows[-1][2],
        "messageCount": len(rows),
    }
    (directory / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    lines = [
        json.dumps({"role": role, "content": content, "createdAt": at, "image": None, "images": [], "memoryTrace": None})
        for role, content, at in rows
    ]
    (directory / "messages.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_export_writes_kourichat_session_files(tmp_path: Path):
    db = open_persistence(profile_scope(home=tmp_path))
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
    db = open_persistence(profile_scope(home=tmp_path))
    try:
        _seed_bot_session(db, session_id, [("user", "first")], time.time())
        assert export_bot_sessions(tmp_path) == 1
        db.messages.append(session_id, NewMessage(role="assistant", content="second reply"))
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
    _write_phone_session(tmp_path, bridge_id, [("user", "from the phone", 1000), ("assistant", "phone reply", 2000)])

    assert import_synced_sessions(tmp_path) == 2

    db = open_persistence(profile_scope(home=tmp_path))
    try:
        # One Bot history, not a new row in the session list: the conversation went into
        # the canonical "Bot Chat" session the product presents.
        row = db.sessions.resolve_by_title("Bot Chat")
        assert row
        messages = db.messages.read_transcript(row)
        assert [(m.role, m.content) for m in messages] == [("user", "from the phone"), ("assistant", "phone reply")]
        assert all(abs(m.timestamp * 1000 - ms) < 1 for m, ms in zip(messages, (1000, 2000)))
        assert db.sessions.get(bridge_id) is None
        # profile_name is backfilled so the owner ladder can route the session.
        assert db.sessions.get(row).profile_name
    finally:
        db.close()


def test_import_folds_several_phone_sessions_into_one_history(tmp_path: Path):
    _write_phone_session(tmp_path, "phone-a", [("user", "a1", 1000), ("assistant", "a2", 2000)])
    _write_phone_session(tmp_path, "phone-b", [("user", "b1", 1500), ("assistant", "b2", 2500)])

    assert import_synced_sessions(tmp_path) == 4

    db = open_persistence(profile_scope(home=tmp_path))
    try:
        row = db.sessions.resolve_by_title("Bot Chat")
        assert row
        assert [m.content for m in db.messages.read_transcript(row)] == ["a1", "b1", "a2", "b2"]
        assert db.sessions.get("phone-a") is None and db.sessions.get("phone-b") is None
    finally:
        db.close()


def test_import_is_idempotent_and_merges(tmp_path: Path):
    _write_phone_session(tmp_path, "phone", [("user", "hello", 1000)])

    assert import_synced_sessions(tmp_path) == 1
    assert import_synced_sessions(tmp_path) == 0  # same file again: nothing to add

    _write_phone_session(tmp_path, "phone", [("user", "hello", 1000), ("assistant", "welcome", 2000)])
    assert import_synced_sessions(tmp_path) == 1  # only the fresh tail

    db = open_persistence(profile_scope(home=tmp_path))
    try:
        row = db.sessions.resolve_by_title("Bot Chat")
        messages = db.messages.read_transcript(row)
        assert [(m.role, m.content) for m in messages] == [("user", "hello"), ("assistant", "welcome")]
    finally:
        db.close()


def test_import_appends_to_an_existing_bot_history(tmp_path: Path):
    db = open_persistence(profile_scope(home=tmp_path))
    try:
        db.maintenance.import_sessions([{
            "id": "20260101_000000_botcha",
            "source": "desktop",
            "title": "Bot Chat",
            "started_at": 500.0,
            "messages": [{"role": "user", "content": "local first", "timestamp": 500.0}],
        }])
    finally:
        db.close()

    _write_phone_session(tmp_path, "phone", [("user", "from the phone", 600_000)])
    assert import_synced_sessions(tmp_path) == 1

    db = open_persistence(profile_scope(home=tmp_path))
    try:
        row = db.sessions.resolve_by_title("Bot Chat")
        assert [m.content for m in db.messages.read_transcript(row)] == ["local first", "from the phone"]
        assert db.sessions.get("phone") is None
    finally:
        db.close()


def test_import_skips_non_bot_and_malformed_rows(tmp_path: Path):
    session_dir = tmp_path / "sessions" / "phone"
    session_dir.mkdir(parents=True)
    rows = [
        json.dumps({"role": "tool", "content": "internal", "createdAt": 1}),
        json.dumps({"role": "user", "content": "visible", "createdAt": 2000}),
        "{ broken json",
        json.dumps({"role": "user", "content": "", "createdAt": 3000}),  # empty text
    ]
    (session_dir / "messages.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")

    assert import_synced_sessions(tmp_path) == 1
    db = open_persistence(profile_scope(home=tmp_path))
    try:
        row = db.sessions.resolve_by_title("Bot Chat")
        assert [m.content for m in db.messages.read_transcript(row)] == ["visible"]
    finally:
        db.close()
