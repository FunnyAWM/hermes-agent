"""Chat-transcript bridge between the Bot's own session store and the device chain.

Android PRD v3.0 (同 02 修订) brings chat sessions into the sync whitelist as
``sessions/<id>/{meta.json,messages.jsonl}``. This module is the PC-side seam:

- ``export_bot_sessions`` writes the Bot's own user/assistant conversations (state.db,
  the main-loop surfaces only) as Kouri Harness session files so a phone pulling from
  this master — or the next negotiation when this device is the secondary — carries them.
- ``import_synced_sessions`` folds every session file a negotiation landed into the
  **canonical Bot history** (the single ``Bot Chat`` session the product presents), never
  into a new row in the session list: the Bot has one history, and a phone's chats are
  part of it.

Both directions use the exact Android ``SessionStore`` JSON shapes (``meta.json`` five
fields; one ``ChatMessage`` per JSONL line with every kotlinx default serialized) and the
same merge semantics as the Android ``mergeMessages``: a message is identified by
role + content + createdAt, so repeated syncs never duplicate rows.
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path

from sync.scope import _SESSION_MESSAGES_NAME, _SESSION_META_NAME

#: Main-loop surfaces only: subagent/cron/test/internal sessions never leave the machine.
_BOT_SOURCES = ("cli", "tui", "desktop", "webui", "gui", "gateway")
_BOT_ROLES = ("user", "assistant")

#: The single Bot history every synced conversation is folded into. Must stay equal to
#: ``tools.bot_mode_probe.BOT_CHAT_TITLE`` (the desktop Bot plugin adopts that title).
_BOT_CHAT_TITLE = "Bot Chat"

#: KouriChat ``SessionStore`` file names (kept in scope.py's whitelist in lockstep).
_STATE_FILE = "session-bridge.json"

# Per-call ceilings mirror hermes_state's import limits; bigger transcripts chunk.
_IMPORT_MAX_MESSAGES = 10_000


def export_bot_sessions(root: Path) -> int:
    """Write the Bot's conversations as session files; returns newly exported messages."""
    from hermes_state import SessionDB

    db = SessionDB()
    try:
        sessions = db.list_sessions_rich(sources=list(_BOT_SOURCES), limit=500, min_message_count=1)
        # The Bot has one history: once the canonical ``Bot Chat`` exists, that session is
        # what the chain carries. Other desktop sessions (scratch work, subagent watch,
        # imports) stay local. Without a Bot Chat — a plain, non-Bot profile — every
        # main-loop session keeps syncing, exactly as before.
        bot_history = _bot_history_session_id(db)
        if bot_history:
            sessions = [row for row in sessions if str(row.get("id") or "") == bot_history]
        state = _load_state(root)
        exported = 0
        for row in sessions:
            session_id = str(row.get("id") or "")
            if not session_id:
                continue
            messages = _bot_messages(db, session_id)
            if not messages:
                continue
            entry = state.get(session_id)
            already = int(entry.get("exported_count") or 0) if entry else 0
            known_file = entry is not None and (root / "sessions" / str(entry.get("uuid") or "") / _SESSION_META_NAME).is_file()
            if known_file and already == len(messages):
                continue
            bridge_id = str(entry.get("uuid") or "") if entry else ""
            if not _valid_uuid(bridge_id):
                bridge_id = str(uuid.uuid4())
            _write_session_files(root, bridge_id, messages, title=str(row.get("title") or ""))
            state[session_id] = {"uuid": bridge_id, "exported_count": len(messages)}
            exported += max(0, len(messages) - already)
        _save_state(root, state)
        return exported
    finally:
        db.close()


def import_synced_sessions(root: Path) -> int:
    """Fold every landed session file into the canonical Bot history; returns the number
    of messages added.

    The Bot has ONE history. A phone's chats must read as part of it, not as a fresh row
    in the desktop session list, so every transcript under ``HERMES_HOME/sessions/`` is
    merged into the ``Bot Chat`` session (created on first sync if the Bot has none yet)
    instead of being imported as its own session. Existing messages merge by
    role+content+createdAt (the Android ``mergeMessages`` identity), so a repeated sync
    never duplicates."""
    from hermes_state import SessionDB

    sessions_dir = Path(root) / "sessions"
    if not sessions_dir.is_dir():
        return 0
    transcripts = []
    for directory in sorted(sessions_dir.iterdir()):
        if not directory.is_dir() or directory.name.startswith("."):
            continue
        transcript = _read_session_files(directory)
        if transcript is not None:
            transcripts.append(transcript)
    if not transcripts:
        return 0

    # One chronological stream: files are per-device, so interleave them by createdAt
    # (stable sort keeps each file's own order for identical timestamps).
    merged: list[dict] = []
    seen: set[tuple[str, str, int]] = set()
    for _meta, messages in transcripts:
        for message in messages:
            identity = (message["role"], message["content"], message["createdAt"])
            if identity in seen:
                continue
            seen.add(identity)
            merged.append(message)
    merged.sort(key=lambda message: message["createdAt"])

    db = SessionDB()
    try:
        target_id = _bot_history_session_id(db)
        if target_id is None:
            return _create_bot_history(db, merged)
        return _merge_into_existing(db, target_id, db.get_messages(target_id), merged)
    finally:
        db.close()


def _bot_history_session_id(db) -> str | None:
    """The id of the canonical ``Bot Chat`` session, resolved through its lineage tip
    (compression rotates ids) — ``None`` when this Bot has no history yet."""
    try:
        resolved = db.resolve_session_by_title(_BOT_CHAT_TITLE)
    except Exception as error:  # health: allow BLE001 -- resolution is best-effort; the caller falls back to creating the history
        logging.getLogger(__name__).debug("bot chat resolution skipped: %s", error)
        resolved = None
    if resolved:
        return str(resolved)
    row = None
    try:
        row = db.get_session_by_title(_BOT_CHAT_TITLE)
    except Exception as error:  # health: allow BLE001 -- same best-effort lookup as above
        logging.getLogger(__name__).debug("bot chat title lookup skipped: %s", error)
    if row:
        return str(row.get("id") or "") or None
    return None


def _create_bot_history(db, messages: list[dict]) -> int:
    """First sync on a Bot with no history: the canonical session IS the imported
    conversation, titled ``Bot Chat`` so the Bot plugin adopts it as the one history."""
    session_id = _new_session_id()
    payload = [
        {"role": message["role"], "content": message["content"], "timestamp": message["createdAt"] / 1000}
        for message in messages
    ]
    first_chunk, rest = payload[:_IMPORT_MAX_MESSAGES], payload[_IMPORT_MAX_MESSAGES:]
    result = db.import_sessions(
        [
            {
                "id": session_id,
                "source": "desktop",
                "title": _BOT_CHAT_TITLE,
                "started_at": messages[0]["createdAt"] / 1000,
                "messages": first_chunk,
            }
        ]
    )
    if not result.get("imported"):
        return 0
    while rest:
        chunk, rest = rest[:_IMPORT_MAX_MESSAGES], rest[_IMPORT_MAX_MESSAGES:]
        db.append_messages_batch(session_id, chunk)
    stamp_profile_owner(db)
    return len(payload)


def _new_session_id() -> str:
    """The store's id convention (``YYYYMMDD_HHMMSS_xxxxxx``) so the Bot history sorts
    and routes like every other session."""
    from datetime import datetime

    return f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"


def _bot_messages(db, session_id: str) -> list[dict]:
    """The session's user/assistant text turns as KouriChat ``ChatMessage`` dicts."""
    rows = db.get_messages(session_id)
    pairs = []
    for row in rows:
        role = str(row.get("role") or "")
        content = row.get("content")
        if role not in _BOT_ROLES or not isinstance(content, str) or not content:
            continue
        pairs.append(
            {
                "role": role,
                "content": content,
                "createdAt": _ms(row.get("timestamp")),
                "image": None,
                "images": [],
                "memoryTrace": None,
            }
        )
    return pairs


def _write_session_files(root: Path, bridge_id: str, messages: list[dict], *, title: str) -> None:
    directory = Path(root) / "sessions" / bridge_id
    directory.mkdir(parents=True, exist_ok=True)
    first = messages[0]
    last = messages[-1]
    first_user = next((message for message in messages if message["role"] == "user"), None)
    meta = {
        "id": bridge_id,
        "title": (title or (first_user or first)["content"][:20]),
        "createdAt": first["createdAt"],
        "updatedAt": last["createdAt"],
        "messageCount": len(messages),
    }
    # Same write shape as the Android SessionStore: temp file + atomic replace.
    _atomic_write(directory / _SESSION_META_NAME, json.dumps(meta, ensure_ascii=False))
    payload = "".join(json.dumps(message, ensure_ascii=False) + "\n" for message in messages)
    _atomic_write(directory / _SESSION_MESSAGES_NAME, payload)


def _read_session_files(directory: Path):
    meta_file = directory / _SESSION_META_NAME
    messages_file = directory / _SESSION_MESSAGES_NAME
    if not messages_file.is_file():
        return None
    try:
        # utf-8-sig: tolerate a BOM a phone editor may have left on meta.json.
        meta = json.loads(meta_file.read_text(encoding="utf-8-sig")) if meta_file.is_file() else {}
        raw_lines = messages_file.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, ValueError):
        return None
    pairs = []
    for line in raw_lines:
        # A half-written tail line is skipped, not fatal (Android readMessages keeps the
        # parseable prefix); so are non-bot and empty rows.
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = message.get("content")
        if role not in _BOT_ROLES or not isinstance(content, str) or not content:
            continue
        pairs.append({"role": role, "content": content, "createdAt": _ms_of_wire(message.get("createdAt"))})
    if not pairs:
        return None
    return meta, pairs


def _merge_into_existing(db, bridge_id: str, existing: list[dict], messages: list[dict]) -> int:
    """A session both ends know: append only messages the Bot has never seen, using the
    Android merge identity (role + content + createdAt)."""
    seen = {
        (str(row.get("role")), str(row.get("content")), _ms(row.get("timestamp")))
        for row in existing
    }
    fresh = [
        {"role": message["role"], "content": message["content"], "timestamp": message["createdAt"] / 1000}
        for message in messages
        if (message["role"], message["content"], message["createdAt"]) not in seen
    ]
    if not fresh:
        return 0
    added = 0
    while fresh:
        chunk, fresh = fresh[:_IMPORT_MAX_MESSAGES], fresh[_IMPORT_MAX_MESSAGES:]
        added += db.append_messages_batch(bridge_id, chunk)
    return added


def stamp_profile_owner(db) -> None:
    """``import_sessions`` leaves ``profile_name`` NULL; stamp this store's own profile
    so the fail-closed owner ladder can route the imported sessions."""
    try:
        from hermes_constants import get_hermes_home, profile_name_for_home

        name = profile_name_for_home(get_hermes_home())
        if name:
            db.backfill_null_session_profiles(name)
    except Exception as error:  # health: allow BLE001,S110 -- ownership stamping is best-effort; the sessions themselves are already in, and the failure is logged
        logging.getLogger(__name__).debug("profile ownership stamping skipped: %s", error)


def _ms(value) -> int:
    """Android stores milliseconds; hermes stores epoch-seconds floats."""
    try:
        return round(float(value) * 1000)
    except (TypeError, ValueError):
        return 0


def _ms_of_wire(value) -> int:
    """A ``createdAt`` straight off the wire is already milliseconds — no conversion."""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _valid_uuid(value) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def _atomic_write(target: Path, payload: str) -> None:
    temp = target.with_name(f".{target.name}.tmp")
    temp.write_text(payload, encoding="utf-8", newline="\n")
    temp.replace(target)


def _load_state(root: Path) -> dict:
    state_file = Path(root) / ".meta" / _STATE_FILE
    if not state_file.is_file():
        return {}
    try:
        data = json.loads(state_file.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_state(root: Path, state: dict) -> None:
    state_file = Path(root) / ".meta" / _STATE_FILE
    state_file.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(state_file, json.dumps(state, ensure_ascii=False, indent=1))
