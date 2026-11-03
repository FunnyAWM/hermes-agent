"""Verify the shared T-10 sample with real memory and notes readers; no model calls."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "evidence" / "fixtures" / "memory-comparison"


def main() -> int:
    sys.path.insert(0, str(ROOT))
    questions = json.loads((SAMPLE / "questions.json").read_text(encoding="utf-8-sig"))
    assert len(questions) == 10 and len({row["id"] for row in questions}) == 10
    for row in questions:
        source = "\n".join((SAMPLE / "home" / path).read_text(encoding="utf-8-sig") for path in row["sources"])
        assert row["question"] and row["reject"]
        assert row["expected"] and all(fact in source for fact in row["expected"]), row["id"]

    with tempfile.TemporaryDirectory(prefix="hermes-t10-") as tmp:
        home = Path(tmp) / "home"
        shutil.copytree(SAMPLE / "home", home)
        os.environ["HERMES_HOME"] = str(home)
        from agent.system_prompt import _memory_parts
        from hermes_notes import NotesStore
        from tools.memory_tool_store import ENTRY_DELIMITER, MemoryStore

        memory = MemoryStore()
        memory.load_from_disk()
        for target, entries, limit in (("memory", memory.memory_entries, memory.memory_char_limit),
                                       ("user", memory.user_entries, memory.user_char_limit)):
            count = len(ENTRY_DELIMITER.join(entries))
            assert count <= limit
            print(f"PASS {target}: {count}/{limit} chars, {len(entries)} entries")
        notes = NotesStore(home)
        entries = notes.list_entries()
        assert len(entries) == 3
        assert [entry.updated for entry in entries] == sorted((entry.updated for entry in entries), reverse=True)
        parts = _memory_parts(SimpleNamespace(
            _memory_store=memory, _memory_enabled=True, _user_profile_enabled=True, _memory_manager=None,
        ))
        assert parts[:2] == [memory.format_for_system_prompt("memory"), memory.format_for_system_prompt("user")]
        assert parts[2] == notes.render_index()
        for entry in entries:
            assert entry.display_line in parts[2]
        assert "首版发布日期尚未确定" not in parts[2]
        print("PASS new-session prompt: MEMORY -> USER -> notes summaries; note bodies require reading")

    for path in sorted(SAMPLE.rglob("*")):
        if path.is_file() and path.name != "README.md":
            print(f"SHA256 {hashlib.sha256(path.read_bytes()).hexdigest()} {path.relative_to(SAMPLE).as_posix()}")
    print("PASS 10 questions: every expected fact is present in its declared sources")
    print("Model answers and Android comparison remain unverified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
