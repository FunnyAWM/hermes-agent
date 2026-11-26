"""Memory panel routes: the four context documents and the notes tree, read and write.

The desktop's Memory page (between New Session and Capabilities) shows exactly what the
Bot loads into a turn — ``SOUL.md``, ``AGENTS.md``, ``memories/USER.md``,
``memories/MEMORY.md`` — plus every note under ``notes/``. A user can edit any of them
here; the panel writes atomically and only inside the whitelist below, so a mistyped
path can never reach a file outside the profile's memory surface.

``notes/docs/`` stays read-only: those are extracted source documents the memory
conventions forbid the Bot to rewrite, and a panel edit would silently desync the note
that cites them.

Every route takes the optional ``profile`` query param and resolves paths inside that
profile's HERMES_HOME, never the launch profile by default.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from hermes_cli.web_routers._common import config_scoped_to_thread

router = APIRouter()

#: The documents every session loads, in the order the panel lists them.
_DOCUMENTS = (
    ("soul", "SOUL.md"),
    ("agents", "AGENTS.md"),
    ("user", "memories/USER.md"),
    ("memory", "memories/MEMORY.md"),
)

#: A single document is a prompt file, not a chat log; anything beyond this is not one.
_MAX_DOC_BYTES = 512 * 1024
#: A note is a note. Beyond this it is a stray artifact, and the panel would choke.
_MAX_NOTE_BYTES = 1024 * 1024
_MAX_NOTES = 2_000

#: Extracted source documents under ``notes/``: the Bot may read them, nobody edits them here.
_READ_ONLY_NOTES_PREFIX = "notes/docs/"


def _home() -> Path:
    from hermes_constants import get_hermes_home

    return Path(str(get_hermes_home()))


def _read_text(path: Path, limit: int) -> tuple[bool, str, int]:
    """``(exists, content, bytes)``; unreadable and oversized files read as empty."""
    try:
        if not path.is_file():
            return False, "", 0
        size = path.stat().st_size
        if size > limit:
            return True, "", size
        return True, path.read_text(encoding="utf-8-sig", errors="replace"), size
    except OSError:
        return False, "", 0


def _normalise(raw: str) -> str:
    """A relative, POSIX-style Markdown path with no traversal — or a 400."""
    relative = raw.strip().replace("\\", "/")
    segments = relative.split("/")
    if not relative or relative.startswith("/") or any(seg in ("", ".", "..") for seg in segments):
        raise HTTPException(status_code=400, detail="invalid path")
    if not relative.endswith(".md"):
        raise HTTPException(status_code=400, detail="only Markdown files are editable")
    return relative


def _writable_target(home: Path, raw: str) -> tuple[Path, int]:
    """Resolve a path to ``(file, size limit)``; anything outside the whitelist is a 400."""
    relative = _normalise(raw)
    if relative in {doc for _, doc in _DOCUMENTS}:
        limit = _MAX_DOC_BYTES
    elif relative.startswith("notes/") and not relative.startswith(_READ_ONLY_NOTES_PREFIX):
        limit = _MAX_NOTE_BYTES
    else:
        raise HTTPException(status_code=400, detail="path is not editable from the memory panel")
    root = home.resolve()
    target = (home / relative).resolve()
    try:
        target.relative_to(root)
    except ValueError as error:
        raise HTTPException(status_code=400, detail="path is outside the memory home") from error
    return target, limit


def _write_atomic(path: Path, content: str) -> int:
    """Replace ``path`` with ``content`` in one rename, so a failed write keeps the old file."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        scratch = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        scratch.write_text(content, encoding="utf-8", newline="")
        os.replace(scratch, path)
        return path.stat().st_size
    except OSError as error:
        raise HTTPException(status_code=500, detail=f"could not save {path.name}") from error


class MemoryFileWrite(BaseModel):
    path: str
    content: str


@router.get("/api/memory/docs")
async def memory_docs(profile: Optional[str] = None) -> dict:
    """The four context documents (inline) plus the notes index (paths only)."""

    def read():
        home = _home()
        files = []
        for identifier, relative in _DOCUMENTS:
            exists, content, size = _read_text(home / relative, _MAX_DOC_BYTES)
            files.append(
                {
                    "id": identifier,
                    "path": relative,
                    "exists": exists,
                    "content": content,
                    "bytes": size,
                    "too_large": size > _MAX_DOC_BYTES,
                    "editable": True,
                }
            )
        notes_dir = home / "notes"
        notes = []
        if notes_dir.is_dir():
            for note in sorted(notes_dir.rglob("*.md")):
                try:
                    if not note.is_file():
                        continue
                    stat = note.stat()
                except OSError:
                    continue
                relative = note.relative_to(home).as_posix()
                notes.append(
                    {
                        "path": relative,
                        "bytes": stat.st_size,
                        "updated_at_ms": int(stat.st_mtime * 1000),
                        "editable": not relative.startswith(_READ_ONLY_NOTES_PREFIX),
                    }
                )
                if len(notes) >= _MAX_NOTES:
                    break
        return {"ok": True, "home": str(home), "files": files, "notes": notes}

    return await config_scoped_to_thread(profile, read)


@router.get("/api/memory/note")
async def memory_note(path: str = Query(min_length=1), profile: Optional[str] = None) -> dict:
    """One note's text. Only ``notes/**.md`` is addressable — no traversal, no memory files."""

    def read():
        home = _home()
        candidate = (home / path).resolve()
        notes_root = (home / "notes").resolve()
        try:
            candidate.relative_to(notes_root)
        except ValueError as error:
            raise HTTPException(status_code=400, detail="path is outside notes/") from error
        if candidate.suffix.lower() != ".md":
            raise HTTPException(status_code=400, detail="only Markdown notes are readable")
        exists, content, size = _read_text(candidate, _MAX_NOTE_BYTES)
        if not exists:
            raise HTTPException(status_code=404, detail="note not found")
        return {
            "ok": True,
            "path": candidate.relative_to(home).as_posix(),
            "content": content,
            "bytes": size,
            "too_large": size > _MAX_NOTE_BYTES,
        }

    return await config_scoped_to_thread(profile, read)


@router.post("/api/memory/doc")
async def memory_write(payload: MemoryFileWrite, profile: Optional[str] = None) -> dict:
    """Save one context document or note. The whitelist above is the whole authority."""

    def write():
        home = _home()
        target, limit = _writable_target(home, payload.path)
        if len(payload.content.encode("utf-8")) > limit:
            raise HTTPException(status_code=413, detail="file is too large to save here")
        size = _write_atomic(target, payload.content)
        return {
            "ok": True,
            "path": target.relative_to(home.resolve()).as_posix(),
            "bytes": size,
            "updated_at_ms": int(target.stat().st_mtime * 1000),
        }

    return await config_scoped_to_thread(profile, write)
