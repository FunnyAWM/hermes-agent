"""Secure, profile-scoped Markdown note scanning and summary rendering."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

CATEGORY_DISPLAY_NAMES: Final[dict[str, str]] = {
    "work": "工作",
    "study": "学习",
    "life": "生活",
    "health": "健康",
    "finance": "财务",
    "people": "人际",
    "other": "其他",
    "docs": "文档",
}
# ``docs`` is an imported-source directory: scanning/indexing includes it, but the
# model may never create notes there (Android 记 04 / D8).
WRITABLE_CATEGORIES: Final[frozenset[str]] = frozenset(CATEGORY_DISPLAY_NAMES) - {"docs"}
# Android 设 13 parity: listing has a soft limit that only gates expiry cleanup, plus an
# optional hard limit that truncates the prompt index (with an explicit omitted count).
DEFAULT_SOFT_LIMIT: Final[int] = 128
DEFAULT_HARD_LIMIT: Final[int | None] = None
_MAX_SUMMARY_CHARS: Final[int] = 40
_FRONT_MATTER_RE: Final[re.Pattern[str]] = re.compile(r"^---\s*\n(?P<body>.*?)(?:\n---\s*\n|\Z)", re.DOTALL)
_FIELD_RE: Final[re.Pattern[str]] = re.compile(r"^(name|description|updated):\s*(.*?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class NoteEntry:
    category: str
    name: str
    summary: str
    path: Path
    updated: str = ""

    @property
    def display_line(self) -> str:
        return f"{CATEGORY_DISPLAY_NAMES[self.category]}/{self.name} — {self.summary}"


class NotesStore:
    """Read and validate notes beneath one Kouri Harness home."""

    def __init__(self, home: Path, *, soft_limit: int = DEFAULT_SOFT_LIMIT,
                 hard_limit: int | None = DEFAULT_HARD_LIMIT) -> None:
        self.home = home
        self.root = home / "notes"
        if soft_limit <= 0:
            raise ValueError("soft_limit must be positive")
        if hard_limit is not None and hard_limit < soft_limit:
            raise ValueError("hard_limit must be >= soft_limit")
        self.soft_limit = soft_limit
        self.hard_limit = hard_limit

    def validate_category(self, category: str, *, for_write: bool = False) -> str:
        if category not in CATEGORY_DISPLAY_NAMES:
            allowed = ", ".join(CATEGORY_DISPLAY_NAMES)
            raise ValueError(f"invalid note category {category!r}; choose one of: {allowed}")
        if for_write and category not in WRITABLE_CATEGORIES:
            raise ValueError(f"note category {category!r} is read-only")
        return category

    def note_path(self, category: str, topic: str) -> Path:
        self.validate_category(category, for_write=True)
        if not topic or Path(topic).name != topic or topic in {".", ".."}:
            raise ValueError("note topic must be a single file name")
        filename = topic if topic.endswith(".md") else f"{topic}.md"
        return self.root / category / filename

    def list_entries(self) -> list[NoteEntry]:
        return self._scan()[1]

    def omitted_count(self) -> int:
        """Notes beyond the index cap — the PRD §7.4 rule is “截断必须明示,不静默丢弃”."""
        total, capped = self._scan()
        return len(total) - len(capped)

    def _scan(self) -> tuple[list[NoteEntry], list[NoteEntry]]:
        if not self.root.is_dir():
            return [], []
        entries: list[NoteEntry] = []
        for path in sorted(self.root.rglob("*.md")):
            if not path.is_file():
                continue
            try:
                relative = path.relative_to(self.root)
                category = relative.parts[0] if len(relative.parts) > 1 else "other"
                if category not in CATEGORY_DISPLAY_NAMES:
                    category = "other"
                fields = self._read_front_matter(path)
            except (OSError, UnicodeError, ValueError):
                continue
            name = fields.get("name") or path.stem
            summary = fields.get("description") or ""
            summary = " ".join(summary.split())[:_MAX_SUMMARY_CHARS]
            if not summary:
                continue
            entries.append(NoteEntry(category, name, summary, path, fields.get("updated", "")))
        entries.sort(key=lambda entry: (entry.updated, entry.path.as_posix()), reverse=True)
        listed = entries if self.hard_limit is None else entries[: self.hard_limit]
        return entries, listed

    def render_index(self) -> str:
        total, entries = self._scan()
        if not entries:
            return ""
        lines = ["NOTES INDEX (names and summaries only)"]
        lines.extend(f"- {entry.display_line}" for entry in entries)
        omitted = len(total) - len(entries)
        if omitted > 0:
            lines.append(f"(-{omitted} older notes beyond this index's {self.hard_limit}-entry hard cap; "
                         "list notes/<category>/ to read one)")
        return "\n".join(lines)

    @staticmethod
    def _read_front_matter(path: Path) -> dict[str, str]:
        # 按二进制行读取，避免文本流预先解码头部之后的正文。
        with path.open("rb") as stream:
            first = stream.readline().decode("utf-8-sig")
            if first.rstrip() != "---":
                return {}
            lines = [first]
            for raw_line in stream:
                line = raw_line.decode("utf-8")
                lines.append(line)
                if line.rstrip() == "---":
                    break
        return NotesStore._front_matter_fields("".join(lines))

    @staticmethod
    def _front_matter_fields(text: str) -> dict[str, str]:
        match = _FRONT_MATTER_RE.match(text)
        if not match:
            return {}
        return {key: value for key, value in _FIELD_RE.findall(match.group("body"))}
