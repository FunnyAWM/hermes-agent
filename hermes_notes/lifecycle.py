"""Note lifecycle shared with Android 设 13: expiry cleanup, usage marking, archive-before-delete.

Every mutating step fails closed — a note that cannot be archived is never deleted, and only
strict, unique header values drive automatic actions: ``expires`` must be one complete header
line holding a ``YYYY-MM-DD`` date, ``lastUsed`` one holding plain digits. Anything ambiguous
(duplicate key, value in the body, malformed) reads as unknown and is left alone.

``lastUsed`` from a synced Android profile uses that device's turn numbers, which are meaningless
here, so this module also writes ``lastUsedBy: desktop`` and only trusts values carrying it.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Final, Iterable

logger = logging.getLogger(__name__)

_HEADER_DELIMITER: Final[str] = "---"
_VERSIONS_DIR: Final[str] = ".versions"
_LAST_USED_BY: Final[str] = "lastUsedBy"
_DESKTOP: Final[str] = "desktop"
_DATE_RE: Final[re.Pattern[str]] = re.compile(r"\d{4}-\d{2}-\d{2}")
_TURN_RE: Final[re.Pattern[str]] = re.compile(r"\d+")
_HEAD_LINES: Final[int] = 8

#: Turn number of the user turn in flight; set by the agent loop so a document read can be marked.
_CURRENT_TURN: ContextVar[int | None] = ContextVar("hermes_notes_current_turn", default=None)


@dataclass(frozen=True)
class StaleDocument:
    """One unused ``notes/docs`` entry offered to the user for deletion."""

    path: str
    name: str
    unused_turns: int


def set_current_turn(turn: int) -> None:
    _CURRENT_TURN.set(turn)


def current_turn() -> int | None:
    return _CURRENT_TURN.get()


def _header_lines(text: str) -> list[str] | None:
    """Lines between the two ``---`` markers, or ``None`` when the header is incomplete."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != _HEADER_DELIMITER:
        return None
    for index in range(1, len(lines)):
        if lines[index].strip() == _HEADER_DELIMITER:
            return lines[1:index]
    return None


def _header_fields(header: list[str]) -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = []
    for line in header:
        key, separator, value = line.partition(":")
        if separator and key.strip():
            fields.append((key.strip(), value.strip()))
    return fields


def _strict_value(text: str, key: str) -> str | None:
    header = _header_lines(text)
    if header is None:
        return None
    values = [value for name, value in _header_fields(header) if name == key]
    return values[0] if len(values) == 1 else None


def expiration_date(text: str) -> date | None:
    """Expiry only from one complete, strictly formatted header line."""
    value = _strict_value(text, "expires")
    if value is None or not _DATE_RE.fullmatch(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def last_used_turn(text: str) -> int | None:
    value = _strict_value(text, "lastUsed")
    if value is None or not _TURN_RE.fullmatch(value):
        return None
    return int(value)


def _notes_root(home: Path) -> Path:
    return (home / "notes").resolve()


def _resolve_note(home: Path, raw: str | Path) -> Path | None:
    """A regular ``.md`` under ``notes/``, resolved — anything else reads as absent."""
    candidate = Path(raw)
    candidate = candidate if candidate.is_absolute() else home / candidate
    try:
        resolved = candidate.resolve()
        resolved.relative_to(_notes_root(home))
    except (OSError, ValueError):
        return None
    if resolved.suffix.lower() != ".md" or not resolved.is_file():
        return None
    return resolved


def _all_notes(home: Path) -> list[Path]:
    root = _notes_root(home)
    if not root.is_dir():
        return []
    return [path for path in sorted(root.rglob("*.md"))
            if path.is_file() and not path.name.startswith(".")]


def archive_note(home: Path, path: Path) -> Path:
    """Copy one note into ``<home>/.versions/<flattened path>.<epoch ms>`` before it is removed."""
    versions = home / _VERSIONS_DIR
    versions.mkdir(parents=True, exist_ok=True)
    label = path.resolve().relative_to(home.resolve()).as_posix().replace("/", "_")
    stamp = int(datetime.now().timestamp() * 1000)
    target = versions / f"{label}.{stamp}"
    while target.exists():
        stamp += 1
        target = versions / f"{label}.{stamp}"
    shutil.copy2(path, target)
    return target


def _atomic_write(path: Path, content: str) -> None:
    scratch = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    scratch.write_text(content, encoding="utf-8", newline="")
    os.replace(scratch, path)


def archive_before_overwrite(home: Path, path: str | Path) -> Path | None:
    """Snapshot an existing model-writable file before it is rewritten.

    Surface: ``memories/{MEMORY,USER}.md`` and ``notes/**.md``; anything else (``SOUL.md``,
    docs outside ``notes/``, missing files) returns ``None`` and is left to the caller's guard.
    """
    try:
        resolved = Path(path).resolve()
        relative = resolved.relative_to(home.resolve()).as_posix()
    except (OSError, ValueError):
        return None
    memory_surface = relative in {"memories/MEMORY.md", "memories/USER.md"}
    if not memory_surface and not (relative.startswith("notes/") and relative.lower().endswith(".md")):
        return None
    if not resolved.is_file():
        return None
    return archive_note(home, resolved)


def remove_expired_notes(home: Path, *, soft_limit: int, enabled: bool = True,
                         today: date | None = None) -> int:
    """Delete explicitly expired notes, only while the directory is over ``soft_limit``.

    Archive first, delete second: a failed archive keeps the note. Returns how many were removed.
    """
    if not enabled:
        return 0
    notes = _all_notes(home)
    if len(notes) <= soft_limit:
        return 0
    current = today or date.today()
    removed = 0
    for note in notes:
        try:
            text = note.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        expires = expiration_date(text)
        if expires is None or expires >= current:
            continue
        try:
            archive_note(home, note)
        except OSError as error:
            logger.warning("note expiry kept %s: archive failed (%s)", note, error)
            continue
        try:
            note.unlink()
        except OSError as error:
            logger.warning("note expiry kept %s: delete failed (%s)", note, error)
            continue
        removed += 1
    return removed


def _render(text: str, fields: list[tuple[str, str]]) -> str:
    header = _header_lines(text)
    closing = text.splitlines()
    close_index = next(index for index in range(1, len(closing)) if closing[index].strip() == _HEADER_DELIMITER)
    body = "\n".join(closing[close_index + 1:]).lstrip("\n")
    rendered = "\n".join(f"{key}: {value}" for key, value in fields)
    return f"{_HEADER_DELIMITER}\n{rendered}\n{_HEADER_DELIMITER}\n\n{body}" if header is not None else body


def mark_documents_used(home: Path, paths: Iterable[str | Path], turn: int) -> int:
    """Stamp ``lastUsed``/``lastUsedBy`` on ``notes/docs`` entries read this turn (设 13)."""
    if turn <= 0:
        return 0
    docs_root = _notes_root(home) / "docs"
    touched = 0
    for raw in paths:
        note = _resolve_note(home, raw)
        if note is None or note.parent != docs_root:
            continue
        try:
            text = note.read_text(encoding="utf-8-sig")
        except OSError:
            continue
        header = _header_lines(text)
        if header is None:
            continue
        fields = _header_fields(header)
        already = (
            last_used_turn(text) == turn
            and _strict_value(text, _LAST_USED_BY) == _DESKTOP
        )
        if already:
            continue
        fields = [(key, value) for key, value in fields if key not in ("lastUsed", _LAST_USED_BY)]
        fields.extend([("lastUsed", str(turn)), (_LAST_USED_BY, _DESKTOP)])
        try:
            _atomic_write(note, _render(text, fields))
        except OSError as error:
            logger.warning("document usage not recorded for %s: %s", note, error)
            continue
        touched += 1
    return touched


def stale_documents(home: Path, *, turn: int, unused_turns: int) -> list[StaleDocument]:
    """``notes/docs`` entries unused for at least ``unused_turns`` desktop turns.

    Unknown or foreign ``lastUsed`` (synced from Android) never qualifies — deletion is always
    offered to the user, never performed here.
    """
    if unused_turns <= 0 or turn <= 0:
        return []
    docs_root = _notes_root(home) / "docs"
    if not docs_root.is_dir():
        return []
    candidates: list[StaleDocument] = []
    for note in sorted(docs_root.glob("*.md")):
        if not note.is_file() or note.name.startswith("."):
            continue
        try:
            with note.open("r", encoding="utf-8-sig", errors="replace") as stream:
                head = "".join(next(stream, "") for _ in range(_HEAD_LINES))
        except OSError:
            continue
        last_used = last_used_turn(head)
        if last_used is None or _strict_value(head, _LAST_USED_BY) != _DESKTOP:
            continue
        unused = turn - last_used
        if unused < unused_turns:
            continue
        candidates.append(StaleDocument(f"notes/docs/{note.name}", note.stem, unused))
    return sorted(candidates, key=lambda item: item.unused_turns, reverse=True)


def delete_documents(home: Path, paths: Iterable[str | Path]) -> int:
    """User-confirmed removal: archive each document first; a failed archive keeps it."""
    docs_root = _notes_root(home) / "docs"
    removed = 0
    for raw in paths:
        note = _resolve_note(home, raw)
        if note is None or note.parent != docs_root:
            continue
        try:
            archive_note(home, note)
        except OSError as error:
            logger.warning("document kept %s: archive failed (%s)", note, error)
            continue
        try:
            note.unlink()
        except OSError as error:
            logger.warning("document kept %s: delete failed (%s)", note, error)
            continue
        removed += 1
    return removed


def note_document_read(path: str | Path) -> None:
    """Record a model read of ``notes/docs`` for the current turn; a metrics miss is not fatal."""
    turn = current_turn()
    if not turn:
        return
    try:
        from hermes_constants import get_hermes_home

        mark_documents_used(get_hermes_home(), [path], turn)
    except (ImportError, OSError, ValueError) as error:
        logger.debug("document usage skipped for %s: %s", path, error)
