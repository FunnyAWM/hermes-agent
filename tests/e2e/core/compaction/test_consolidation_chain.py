"""T-05: the long-conversation chain on the real agent, SessionDB and file tools.

Everything real except the model: a real ``AIAgent`` against the recording loopback provider, the
real ``memory`` / ``write_file`` tools, real preflight compaction and a real on-disk session.

The chain this proves (aligned with Android T-81, which does NOT require extracting facts in the
same compression round):

1. durable facts live in ``notes/<category>/<topic>.md``, written by an ordinary memory/tool round;
2. ``MEMORY.md`` stays a short recent summary and refuses an entry that would pass its 3,000-char
   limit, reporting the real count and the limit without touching the file;
3. compression past the trigger keeps the session id, archives old rows in place, and the rebuilt
   prompt still carries MEMORY -> USER -> NOTES INDEX (names only, never note bodies);
4. a summarizer that refuses to summarize commits nothing: the live context, ``MEMORY.md`` and the
   note file are byte-identical afterwards.
"""

from __future__ import annotations

import sqlite3

from hermes_charcount import char_count
from tests.e2e.core.compaction._helpers import (
    REFUSAL_TEXT,
    Text,
    ToolCall,
    TurnSpec,
    db_rows,
    warned,
)

SUMMARY_BLOB = "RECENT SUMMARY: shipping the NSIS installer " + ("next step pending. " * 140)
NOTE_BODY = "NOTE_BODY: an NSIS overwrite install keeps memories/ and notes/ in place."
NOTE_TEXT = (
    "---\n"
    "name: 打包方案\n"
    "description: Windows 安装与升级方案\n"
    "updated: 2026-11-06\n"
    "---\n"
    f"{NOTE_BODY}\n"
)
PASTE = " ".join(f"w{i}" for i in range(2400))


def _session_rows(sc) -> int:
    conn = sqlite3.connect(f"file:{sc.db_path}?mode=ro", uri=True, timeout=30)
    try:
        return conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    finally:
        conn.close()


def _note_path(sc):
    return sc.home / "notes" / "work" / "packaging.md"


def _consolidation_turn(sc) -> TurnSpec:
    return TurnSpec(marker="U05x0xc0ffee", user="U05x0xc0ffee consolidate what we decided",
                    kind="consolidation", steps=[
                        ToolCall("memory", {"target": "memory",
                                            "operations": [{"action": "add", "content": SUMMARY_BLOB}]}),
                        ToolCall("memory", {"target": "memory",
                                            "operations": [{"action": "add",
                                                            "content": "OVERFLOW_MARK " + "x" * 400}]}),
                        ToolCall("write_file", {"path": str(_note_path(sc)), "content": NOTE_TEXT}),
                        Text("noted"),
                    ])


def _paste_turn(marker: str) -> TurnSpec:
    return TurnSpec(marker=marker, user=f"{marker} large paste to keep in mind: {PASTE}",
                    kind="paste", steps=[Text(f"seen {marker}")])


def test_memory_refusal_then_note_then_compaction(make_scenario):
    assert char_count(SUMMARY_BLOB) < 3000
    sc = make_scenario("good", toolsets=("file", "memory"))
    (sc.home / "memories").mkdir(parents=True, exist_ok=True)
    (sc.home / "memories" / "USER.md").write_text("USER PREF: terse answers.", encoding="utf-8")
    sc.build_agent()
    assert sc.agent._memory_enabled and sc.agent._memory_store is not None

    # Turn 1: a normal memory/tool round writes the durable fact and the recent summary.
    sc.run_turn(_consolidation_turn(sc))
    memory_text = (sc.home / "memories" / "MEMORY.md").read_text(encoding="utf-8")
    assert "RECENT SUMMARY" in memory_text
    assert "OVERFLOW_MARK" not in memory_text
    assert char_count(memory_text) <= 3000
    refusal = [m for m in sc.history
               if m.get("role") == "tool" and "would exceed the limit by" in str(m.get("content"))]
    assert refusal, "the over-limit add was not refused in the tool round"
    assert "3,000" in str(refusal[0]["content"])
    assert f"{char_count(SUMMARY_BLOB.strip()):,}" in str(refusal[0]["content"])
    note_text = _note_path(sc).read_text(encoding="utf-8")
    assert note_text == NOTE_TEXT

    # Turn 2 grows the history; turn 3 crosses the preflight threshold and compacts.
    sc.run_turn(_paste_turn("U05x0xab12cd"))
    sc.base_system = None  # this turn rebuilds the prompt from the now-populated memory files
    rec = sc.run_turn(_paste_turn("U05x0xdef456"))

    assert rec.summary_calls >= 1
    assert sc.committed_compactions() >= 1
    assert sc.agent.session_id == sc.session_id
    assert _session_rows(sc) == 1
    rows = db_rows(sc.db_path, sc.session_id)
    assert any(r["compacted"] for r in rows), "compaction archived nothing"
    assert any(not r["active"] for r in rows)
    # The durable files are untouched by compaction.
    assert (sc.home / "memories" / "MEMORY.md").read_text(encoding="utf-8") == memory_text
    assert _note_path(sc).read_text(encoding="utf-8") == NOTE_TEXT
    # The rebuilt prompt keeps the memory order and injects names only.
    prompt = rec.main_requests[0]["messages"][0]["content"]
    assert prompt.index("MEMORY (your personal notes)") < prompt.index("USER PROFILE") < prompt.index("NOTES INDEX")
    assert "工作/打包方案 — Windows 安装与升级方案" in prompt
    assert "RECENT SUMMARY" in prompt
    assert NOTE_BODY not in prompt


def test_failed_summary_keeps_context_and_files(make_scenario):
    sc = make_scenario("refusal", toolsets=("file", "memory"))
    note = _note_path(sc)
    note.parent.mkdir(parents=True)
    note.write_text(NOTE_TEXT, encoding="utf-8")
    memory_file = sc.home / "memories" / "MEMORY.md"
    memory_file.parent.mkdir(parents=True, exist_ok=True)
    memory_file.write_text("EXISTING_SUMMARY: keeps its exact bytes.", encoding="utf-8")
    note_before, memory_before = note.read_bytes(), memory_file.read_bytes()
    sc.build_agent()

    first = "U05x1xfacade keep this turn alive while the summarizer fails"
    sc.run_turn(TurnSpec(marker="U05x1xfacade", user=first, kind="anchor", steps=[
        ToolCall("read_file", {"path": str(note)}),
        ToolCall("read_file", {"path": str(memory_file)}),
        Text("anchored"),
    ]))
    sc.run_turn(_paste_turn("U05x0x1357bd"))
    rec = sc.run_turn(_paste_turn("U05x0x2468ce"))

    assert rec.summary_calls >= 1
    assert warned(sc), "a refused summary was not surfaced to the user"
    assert sc.committed_compactions() == 0
    assert note.read_bytes() == note_before
    assert memory_file.read_bytes() == memory_before
    assert not any(REFUSAL_TEXT in str(m.get("content")) for m in sc.history)
    live = [r for r in db_rows(sc.db_path, sc.session_id) if r["active"] and r["role"] == "user"]
    assert any(first in (r["content"] or "") for r in live), "the live user turn was archived behind a bad summary"
    assert sc.agent.session_id == sc.session_id
