"""Sync whitelist (Android M4 同 02, v3.0 revision includes chat sessions).

Only ``SOUL.md``, the two memory files, all Markdown under ``notes/`` and ``skills/``,
and chat sessions as ``sessions/<id>/{meta.json,messages.jsonl}`` participate;
``AGENTS.md``, ``.versions/``, ``.meta/``, ``.memory_incomplete``, the ``sessions/current``
pointer and dot files never enter the manifest, and databases, settings and keys never
sync. The single source of truth for the path rule is the Android client's
``MemoryRepository.isSyncablePath``; change this port only together with it — the
memory-contract and sync-whitelist tests guard cross-client parity.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

SOUL_PATH = "SOUL.md"
MEMORY_PATH = "memories/MEMORY.md"
USER_PATH = "memories/USER.md"
AGENTS_FILE = "AGENTS.md"

_SESSION_META_NAME = "meta.json"
_SESSION_MESSAGES_NAME = "messages.jsonl"

_FIXED_FILES = (SOUL_PATH, MEMORY_PATH, USER_PATH)
_SCANNED_DIRS = ("notes", "skills")
_SESSIONS_DIR = "sessions"
_EXCLUDED_TOP_DIRS = frozenset({".meta", ".versions"})


@dataclass(frozen=True, slots=True)
class SyncFile:
    relative_path: str
    file: Path


def is_syncable_path(relative_path: str) -> bool:
    """Whether a relative path may participate in sync (Android 唯一口径的镜像)."""
    normalized = relative_path.strip().replace("\\", "/")
    if not normalized or normalized.startswith("/"):
        return False
    if normalized.lower() == AGENTS_FILE:
        return False
    if normalized == ".memory_incomplete":
        return False
    segments = normalized.split("/")
    if any(segment in ("", ".", "..") for segment in segments):
        return False
    if segments[0] == _SESSIONS_DIR:
        # Chat sessions (v3.0): only sessions/<id>/meta.json and messages.jsonl pass; the
        # `current` pointer (2 segments) and dot-prefixed temp/trace files are excluded.
        if len(segments) != 3:
            return False
        if segments[1].startswith("."):
            return False
        return segments[2] in (_SESSION_META_NAME, _SESSION_MESSAGES_NAME)
    if not normalized.lower().endswith(".md"):
        return False
    if normalized in _FIXED_FILES:
        return True
    top = segments[0]
    if top in _EXCLUDED_TOP_DIRS:
        return False
    return top in ("notes", "skills")


def scan(root: Path) -> list[SyncFile]:
    """All currently syncable files, sorted by relative path (forward slashes)."""
    root = Path(root)
    result: list[SyncFile] = []
    for fixed in _FIXED_FILES:
        file = root / fixed
        if file.is_file():
            result.append(SyncFile(fixed, file))
    for directory in _SCANNED_DIRS:
        dir_file = root / directory
        if not dir_file.is_dir():
            continue
        for file in dir_file.rglob("*"):
            if file.is_file() and file.suffix.lower() == ".md":
                relative = file.relative_to(root).as_posix()
                if is_syncable_path(relative):
                    result.append(SyncFile(relative, file))
    sessions_dir = root / _SESSIONS_DIR
    if sessions_dir.is_dir():
        for file in sessions_dir.rglob("*"):
            if file.is_file():
                relative = file.relative_to(root).as_posix()
                if is_syncable_path(relative):
                    result.append(SyncFile(relative, file))
    return sorted(result, key=lambda entry: entry.relative_path)
