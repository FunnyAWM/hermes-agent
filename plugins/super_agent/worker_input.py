"""Materialize long prompts inside an assignment's validated worktree."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Callable


INLINE_CHAR_BUDGET = 8000
_LEGACY_NOTICE = re.compile(
    r"\n\n\[Full content spilled to (spill/(sa-[0-9a-f]{12})/[0-9a-f]{12}\.txt); "
    r"read it from the workspace before continuing\.\]\Z"
)


def initial_prompt(task: str) -> str:
    return ("Complete the task in the current Git worktree. Work autonomously and return a concise "
            f"summary, validation evidence, and changed files.\n\nTask:\n{task}")


def legacy_spill_reference(text: str, initial: str, ticket_id: str) -> str | None:
    if len(initial) <= INLINE_CHAR_BUDGET or not text.startswith(initial[:INLINE_CHAR_BUDGET]):
        return None
    match = _LEGACY_NOTICE.fullmatch(text, INLINE_CHAR_BUDGET)
    return match[1] if match and match[2] == ticket_id else None


def worker_prompt(workspace: Path, turn: dict, initial: str, ticket_id: str, record_input: Callable) -> str:
    text = turn["text"]
    if len(text) <= INLINE_CHAR_BUDGET:
        return text
    if workspace.resolve() != workspace.absolute():
        raise ValueError("Worker input workspace resolves outside its assigned path")
    # Old submissions must retain the exact original RPC payload for input_id replay.
    legacy = legacy_spill_reference(text, initial, ticket_id) if turn["sequence"] == 1 else None
    if legacy:
        body = initial.encode("utf-8")
        destination = workspace / legacy
        for directory in (destination.parent.parent, destination.parent):
            directory.mkdir(exist_ok=True)
            if directory.resolve() != directory.absolute():
                raise ValueError("Worker input directory resolves outside its assigned path")
        prompt = text
    else:
        body = text.encode("utf-8")
        name = f".super-agent-input-{turn['id']}-{hashlib.sha256(body).hexdigest()}.txt"
        destination = workspace / name
        prompt = text[:INLINE_CHAR_BUDGET] + f"\n\n[Read the complete task from {name} in the workspace before continuing.]"
    if destination.parent.resolve() != destination.parent.absolute():
        raise ValueError("Worker input directory resolves outside its assigned path")
    # Link an already complete temporary file. Existing tracked files, links and
    # operator edits must never be overwritten, including on crash recovery.
    pending = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as handle:
            pending = Path(handle.name)
            handle.write(body)
        try:
            os.link(pending, destination)
        except FileExistsError:
            info = destination.lstat()
            if (not stat.S_ISREG(info.st_mode) or destination.resolve() != destination.absolute()
                    or destination.read_bytes() != body):
                raise ValueError(f"Worker input path conflicts with an existing file: {destination}") from None
        else:
            info = pending.stat()
            record_input(turn["id"], destination.relative_to(workspace).as_posix(), str(info.st_dev),
                         str(info.st_ino), hashlib.sha256(body).hexdigest())
    finally:
        if pending is not None:
            pending.unlink(missing_ok=True)
    return prompt


def remove_owned_input(workspace: Path, row) -> None:
    """Keep replaced files, edited inputs, and unverifiable old worktrees intact."""
    relative = Path(row["path"])
    if relative.is_absolute() or relative.drive or ".." in relative.parts:
        return
    path = workspace / relative
    if path.resolve() != path.absolute() or not path.is_file():
        return
    with path.open("rb") as handle:
        info = os.fstat(handle.fileno())
        if (not info.st_ino or str(info.st_dev) != row["device"] or str(info.st_ino) != row["inode"]
                or hashlib.file_digest(handle, "sha256").hexdigest() != row["digest"]):
            return
    if os.path.samestat(info, path.lstat()):
        path.unlink()


def remove_legacy_spill(data_dir: Path, text: str, initial: str, ticket_id: str) -> None:
    """The old store recorded only a prompt reference and wrote in native text mode."""
    ref = legacy_spill_reference(text, initial, ticket_id)
    if not ref:
        return
    path = data_dir / ref
    if path.resolve() != path.absolute() or not path.is_file():
        return
    body = initial.encode("utf-8")
    windows_body = body.replace(b"\n", b"\r\n")
    try:
        with path.open("rb") as handle:
            info = os.fstat(handle.fileno())
            if (not info.st_ino or not stat.S_ISREG(info.st_mode)
                    or info.st_size not in (len(body), len(windows_body))
                    or handle.read(info.st_size + 1) not in (body, windows_body)):
                return
        if os.path.samestat(info, path.lstat()):
            path.unlink()
    except FileNotFoundError:
        return
