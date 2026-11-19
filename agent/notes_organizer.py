"""Optional periodic notes organizer — Android 记 06 整理轮次, notes-only scope.

Default off (``memory.organize_turns: 0``). When enabled it fires once every N user turns,
after the turn's answer is delivered (the finalizer ticks it, matching Android's
finishStream), in a daemon thread using the same cache-parity fork as background review:
the parent's prompt and ``tools[]`` stay untouched, so prompt caching is unaffected.

A pass writes ``notes/.organize-incomplete`` before it runs and clears it only on success,
so a failed or killed pass is retried on any later turn (next turn or next launch), not
just on the next multiple of the interval — Android 记 06 的下一轮补做. Recovery still
requires the interval to stay configured. The marker is a dotfile without a ``.md``
suffix, so no notes scan (index, lifecycle, stale documents) ever picks it up.

Scope is deliberately narrower than background review: this pass only maintains
``notes/<category>/*.md`` (descriptions, duplicates, expiry), never ``MEMORY.md`` / ``USER.md`` /
``SOUL.md``. It coexists with background review, which keeps owning fact extraction.
"""

from __future__ import annotations

import logging
from collections import Counter
from contextlib import suppress
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_MAX_ITERATIONS = 12
_TOOL_NAMES = frozenset({"read_file", "search_files", "write_file", "patch"})
_WRITE_TOOLS = frozenset({"write_file", "patch"})

# Marks the MEMORY.md block in the organizer's final text (Android MemoryOrganizer writes the
# summary back to MEMORY.md; here the code owns the write so it can stay CAS-guarded).
MEMORY_SEPARATOR = "---MEMORY---"
_DEFAULT_MEMORY_CHAR_LIMIT = 3000

ORGANIZER_PROMPT = """Maintain the user's notes. This is a background housekeeping pass, not a
conversation turn: do not greet anyone and do not try to talk to the user.

Rules:
- Change only Markdown notes under notes/<category>/ (work, study, life, health, finance, people,
  other). Never write notes/docs/ (imported source text, read-only), memories/MEMORY.md,
  memories/USER.md, SOUL.md or any file outside notes/.
- Work from the notes index below, then read the notes you intend to change with read_file.
- Improve descriptions that no longer match the body, merge duplicate notes into the newer path,
  and split a note that covers two unrelated topics.
- Add or extend `expires: YYYY-MM-DD` only when the note itself states a deadline; never invent
  dates and never delete a note.
- Keep front matter fields (name, description, updated) accurate; preserve note bodies unless the
  rewrite is the point.
- When there is nothing worth changing, make no tool calls.

Finish with one short plain-text line naming what changed, or "no changes".

Then, on its own line, print exactly """ + MEMORY_SEPARATOR + """ and after it the COMPLETE new
contents of memories/MEMORY.md: one concise, deduplicated long-term memory keeping every fact
still worth remembering. Do not wrap it in a code fence. Keep it under {limit} characters.
When nothing in MEMORY.md should change, omit the separator and the block entirely."""


def _marker_path(agent: Any) -> Path:
    from hermes_constants import get_hermes_home

    return Path(str(get_hermes_home())) / "notes" / ".organize-incomplete"


def _incomplete(agent: Any) -> bool:
    try:
        return _marker_path(agent).is_file()
    except (OSError, ValueError):
        return False


def should_organize(agent: Any) -> bool:
    from hermes_notes.settings import load_notes_settings

    every = load_notes_settings().organize_turns
    if every <= 0:
        return False
    turns = int(getattr(agent, "_user_turn_count", 0) or 0)
    if turns <= 0:
        return False
    # One auxiliary fork at a time: a review already in flight owns this turn's budget.
    if getattr(agent, "_background_review_agent", None) is not None:
        return False
    # A pass in flight owns the marker too; the next turn re-checks.
    if getattr(agent, "_notes_organizer_in_flight", False):
        return False
    # 记 06: due on the interval, or on any turn once a failed pass left the marker
    # (失败下一轮补做) — recovery still requires the interval to stay configured.
    if turns % every and not _incomplete(agent):
        return False
    try:
        from hermes_constants import get_hermes_home

        return (Path(str(get_hermes_home())) / "notes").is_dir()
    except (OSError, ValueError):
        return False


def _memory_path(agent: Any) -> Path:
    from hermes_constants import get_hermes_home

    return Path(str(get_hermes_home())) / "memories" / "MEMORY.md"


def _read_memory(agent: Any) -> str:
    try:
        return _memory_path(agent).read_text(encoding="utf-8")
    except OSError:
        return ""


def _memory_char_limit() -> int:
    try:
        from hermes_cli.config import load_config_readonly

        return int(load_config_readonly().get("memory", {}).get("memory_char_limit", _DEFAULT_MEMORY_CHAR_LIMIT))
    except (TypeError, ValueError, OSError):
        return _DEFAULT_MEMORY_CHAR_LIMIT


def _split_memory_block(text: str) -> Optional[str]:
    """The MEMORY.md body after the separator; ``None`` when the organizer sent none."""
    if MEMORY_SEPARATOR not in (text or ""):
        return None
    body = text.split(MEMORY_SEPARATOR, 1)[1].strip()
    if body.startswith("```"):
        body = body.split("\n", 1)[1] if "\n" in body else ""
        body = body.rsplit("```", 1)[0]
    body = body.strip()
    return body or None


def _commit_memory(agent: Any, expected: str, summary: str) -> bool:
    """CAS-write the organized MEMORY.md (Android ``commitOrganizedMemory``).

    The write is skipped when the file changed while the pass ran, so a concurrent model or
    user edit is never clobbered. Overwrites go through ``archive_before_overwrite``.
    """
    path = _memory_path(agent)
    current = _read_memory(agent)
    if current != expected:
        logger.info("notes organizer: memories/MEMORY.md changed during the pass; keeping it")
        return False
    limit = _memory_char_limit()
    if limit > 0 and len(summary) > limit:
        summary = summary[:limit].rstrip()
    try:
        from hermes_notes.lifecycle import archive_before_overwrite

        archive_before_overwrite(Path(str(path.parent.parent)), path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(summary + "\n", encoding="utf-8")
    except OSError:
        logger.exception("notes organizer: writing memories/MEMORY.md failed")
        return False
    return True


def _notes_context(agent: Any) -> str:
    """Index plus stale-document candidates and the current MEMORY.md, all empty-safe."""
    from hermes_constants import get_hermes_home
    from hermes_notes import NotesStore
    from hermes_notes.lifecycle import stale_documents
    from hermes_notes.settings import load_notes_settings

    home = Path(str(get_hermes_home()))
    index = NotesStore(home).render_index()
    turn = int(getattr(agent, "_user_turn_count", 0) or 0)
    stale = stale_documents(home, turn=turn, unused_turns=load_notes_settings().document_unused_turns)
    lines = [index or "NOTES INDEX: empty"]
    if stale:
        lines.append("STALE DOCUMENTS (user is asked separately; do not delete):")
        lines.extend(f"- {item.path} (unused for {item.unused_turns} turns)" for item in stale)
    memory = _read_memory(agent).strip()
    lines += ["", "CURRENT memories/MEMORY.md:", memory or "(empty)"]
    return "\n".join(lines)


def _summarize_changes(messages: list[dict]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for message in messages:
        for call in message.get("tool_calls") or []:
            name = (call.get("function") or {}).get("name")
            if name in _WRITE_TOOLS:
                counts[str(name)] += 1
    return dict(counts)


def _run_notes_organizer(agent: Any) -> None:
    fork = None
    try:
        from agent.background_review import build_cache_parity_fork
        from hermes_cli.plugins import clear_thread_tool_whitelist, set_thread_tool_whitelist
        from tools.file_tools_write_guards import bind_notes_write_scope

        fork, _rt, _routed = build_cache_parity_fork(
            agent, max_iterations=_MAX_ITERATIONS, write_origin="notes_organizer")
        set_thread_tool_whitelist(
            _TOOL_NAMES,
            deny_msg_fmt=(
                "Notes organizer denied tool: {tool_name}. Allowed here: read_file, search_files, "
                "write_file and patch on notes/<category>/*.md only. Do not retry {tool_name}."),
        )
        try:
            # 记 06: mark before the pass so a crash or kill leaves the recovery marker;
            # only a completed pass clears it.
            with suppress(OSError):
                _marker_path(agent).write_text("incomplete", encoding="utf-8")
            # The CAS baseline must be what the model was shown, not a later re-read.
            memory_before = _read_memory(agent)
            prompt = ORGANIZER_PROMPT.replace("{limit}", str(_memory_char_limit()))
            with bind_notes_write_scope():
                result = fork.run_conversation(
                    user_message=f"{prompt}\n\n{_notes_context(agent)}",
                    conversation_history=[],
                )
        finally:
            clear_thread_tool_whitelist()
        # Android MemoryOrganizer: the pass also rewrites MEMORY.md. Code owns the write so a
        # concurrent edit loses nothing (CAS) and the old file lands in .versions/.
        summary = _split_memory_block(str((result or {}).get("final_response") or ""))
        if summary is not None:
            if _commit_memory(agent, memory_before, summary):
                logger.info("notes organizer rewrote memories/MEMORY.md (%d chars)", len(summary))
        changes = _summarize_changes(list(getattr(fork, "_session_messages", [])))
        if changes:
            summary = " · ".join(f"{name} {count}" for name, count in sorted(changes.items()))
            logger.info("notes organizer made %s", summary)
            safe_print = getattr(agent, "_safe_print", None)
            if callable(safe_print):
                safe_print(f"🗂 笔记整理：{summary}")
        else:
            logger.info("notes organizer made no changes")
        with suppress(OSError):
            _marker_path(agent).unlink(missing_ok=True)
    except Exception:
        # Thread boundary: a failing housekeeping pass must never take the process down.
        # The marker stays; the next turn recovers.
        logger.exception("notes organizer failed")
    finally:
        agent._notes_organizer_in_flight = False
        if fork is not None:
            _release(fork)


def _release(fork: Any) -> None:
    try:
        from agent.background_review import _release_fork_clients

        _release_fork_clients(fork)
    except Exception:
        logger.debug("notes organizer fork release failed", exc_info=True)


def maybe_spawn(agent: Any) -> bool:
    """Start one organizer thread when the turn counter or the recovery marker says it is due."""
    if not should_organize(agent):
        return False
    from agent.memory_provider import spawn_context_thread

    # Set before the thread starts so the next tick cannot double-spawn.
    agent._notes_organizer_in_flight = True
    # spawn_context_thread keeps the caller's profile scope (a raw Thread does not).
    thread = spawn_context_thread(
        _run_notes_organizer, name="hermes-notes-organizer", args=(agent,))
    try:
        thread.start()
    except BaseException:
        agent._notes_organizer_in_flight = False  # a stuck flag would silence the organizer
        raise
    return True


def organizer_settings(agent: Any) -> Optional[int]:
    """Effective interval in user turns, ``None`` when the organizer is off."""
    from hermes_notes.settings import load_notes_settings

    every = load_notes_settings().organize_turns
    return every if every > 0 else None


__all__ = ["ORGANIZER_PROMPT", "maybe_spawn", "organizer_settings", "should_organize"]
