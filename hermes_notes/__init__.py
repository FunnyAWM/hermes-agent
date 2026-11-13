"""Profile-scoped Markdown notes used by the system prompt index."""

from .store import (
    CATEGORY_DISPLAY_NAMES,
    DEFAULT_HARD_LIMIT,
    DEFAULT_SOFT_LIMIT,
    WRITABLE_CATEGORIES,
    NoteEntry,
    NotesStore,
)

__all__ = [
    "CATEGORY_DISPLAY_NAMES",
    "DEFAULT_HARD_LIMIT",
    "DEFAULT_SOFT_LIMIT",
    "WRITABLE_CATEGORIES",
    "NoteEntry",
    "NotesStore",
]
