"""Notes lifecycle settings from ``memory.*`` — Android 设 13 parity.

Read on demand rather than copied onto the agent at init: ``config_defaults.py`` and
``agent_init.py`` are both at their FILE_LINES ratchet, and an unread key is worse than a
lazy read. Malformed values fall back to the documented defaults (fail closed to the safe
behaviour, never to "unlimited" or "delete everything").
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

DEFAULTS: dict[str, Any] = {
    "notes_soft_limit": 128,
    "notes_hard_limit": 0,  # 0 = no hard cap; the index is never truncated
    "remove_expired_notes": True,
    "document_unused_turns": 10,  # 0 = never prompt
    "organize_turns": 0,  # 0 = organizer off
}


@dataclass(frozen=True)
class NotesSettings:
    soft_limit: int
    hard_limit: int | None
    remove_expired_notes: bool
    document_unused_turns: int
    organize_turns: int


def _positive_int(value: Any, fallback: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return number if number > 0 else fallback


def _non_negative_int(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, number)


def _enabled(value: Any, fallback: bool) -> bool:
    if value is None:
        return fallback
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return bool(value)


def _setting_int(section: dict[str, Any], key: str) -> int:
    """Configured non-negative integer, or the documented default when absent."""
    value = section.get(key)
    return _non_negative_int(DEFAULTS[key] if value is None else value)


def load_notes_settings() -> NotesSettings:
    """Current ``memory`` section resolved through the same loader the memory tool uses."""
    section: dict[str, Any] = {}
    try:
        from tools.memory_tool import get_builtin_memory_config

        section = get_builtin_memory_config()
    except (ImportError, OSError, ValueError):
        section = {}
    soft = _positive_int(section.get("notes_soft_limit"), DEFAULTS["notes_soft_limit"])
    hard_raw = _non_negative_int(section.get("notes_hard_limit"))
    hard = hard_raw if hard_raw >= soft else None
    return NotesSettings(
        soft_limit=soft,
        hard_limit=hard,
        remove_expired_notes=_enabled(
            section.get("remove_expired_notes"), DEFAULTS["remove_expired_notes"]),
        document_unused_turns=_setting_int(section, "document_unused_turns"),
        organize_turns=_setting_int(section, "organize_turns"),
    )


__all__ = ["DEFAULTS", "NotesSettings", "load_notes_settings"]
