from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agent.system_prompt import build_system_prompt_parts
from hermes_notes import CATEGORY_DISPLAY_NAMES, DEFAULT_SOFT_LIMIT, NotesStore


def test_notes_store_validates_categories_and_renders_summary_only(tmp_path: Path):
    store = NotesStore(tmp_path)
    assert set(CATEGORY_DISPLAY_NAMES) == {"work", "study", "life", "health", "finance", "people", "other", "docs"}
    try:
        store.note_path("invalid", "topic")
    except ValueError as exc:
        assert "work" in str(exc)
    else:
        raise AssertionError("invalid category was accepted")

    note = store.note_path("work", "roadmap")
    note.parent.mkdir(parents=True)
    note.write_text(
        "---\nname: 路线图\ndescription: 今年的发布计划\nupdated: 2026-10-20\n---\nSECRET BODY\n",
        encoding="utf-8",
    )
    (tmp_path / "notes" / "unknown" ).mkdir()
    (tmp_path / "notes" / "unknown" / "legacy.md").write_text(
        "---\nname: 旧笔记\ndescription: 兼容目录\n---\nLEGACY BODY\n", encoding="utf-8"
    )
    rendered = store.render_index()
    assert "工作/路线图 — 今年的发布计划" in rendered
    assert "其他/旧笔记 — 兼容目录" in rendered
    assert "SECRET BODY" not in rendered
    assert "LEGACY BODY" not in rendered


def test_notes_index_is_after_user_block(tmp_path: Path, monkeypatch):
    note = tmp_path / "notes" / "study" / "book.md"
    note.parent.mkdir(parents=True)
    note.write_text("---\nname: 书\ndescription: 摘要\n---\n正文\n", encoding="utf-8")
    memory = SimpleNamespace(format_for_system_prompt=lambda target: {"memory": "MEMORY", "user": "USER"}.get(target))
    agent = SimpleNamespace(
        load_soul_identity=False,
        skip_context_files=True,
        valid_tool_names=[],
        _task_completion_guidance=False,
        _tool_use_enforcement=False,
        _environment_probe=False,
        _kanban_worker_guidance="",
        _memory_store=memory,
        _memory_manager=None,
        _memory_enabled=True,
        _user_profile_enabled=True,
        model="",
        provider="",
        platform="",
        pass_session_id=False,
        session_id="",
        _emit_status=lambda *_args, **_kwargs: None,
    )
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    with patch("agent.system_prompt._skills_prompt", return_value="SKILLS"):
        volatile = build_system_prompt_parts(agent)["volatile"]
    assert volatile.index("USER") < volatile.index("NOTES INDEX") < volatile.index("SKILLS")


def test_notes_index_lists_everything_until_a_hard_cap_is_set(tmp_path: Path):
    store = NotesStore(tmp_path)
    for index in range(DEFAULT_SOFT_LIMIT + 3):
        path = store.note_path("other", f"note-{index}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\nname: {index}\ndescription: item {index}\nupdated: 2026-10-{index % 28 + 1:02d}\n---\n", encoding="utf-8")
    assert len(store.list_entries()) == DEFAULT_SOFT_LIMIT + 3
    capped = NotesStore(tmp_path, soft_limit=DEFAULT_SOFT_LIMIT, hard_limit=DEFAULT_SOFT_LIMIT)
    assert len(capped.list_entries()) == DEFAULT_SOFT_LIMIT


def test_notes_index_announces_omitted_entries(tmp_path: Path):
    # PRD §7.4: 被截断的条数必须明示,不静默丢弃. 软上限本身不截断，硬上限才截断。
    store = NotesStore(tmp_path, soft_limit=10, hard_limit=10)
    assert store.render_index() == ""
    assert store.omitted_count() == 0
    for index in range(12):
        path = store.note_path("other", f"note-{index}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\nname: n{index}\ndescription: d{index}\nupdated: 2026-10-{index % 28 + 1:02d}\n---\n", encoding="utf-8")
    rendered = store.render_index()
    assert "(-2 older notes beyond this index's 10-entry hard cap" in rendered
    assert len(store.list_entries()) == 10
    assert store.omitted_count() == 2


def test_notes_index_keeps_newest_note_even_when_path_sorts_last(tmp_path: Path):
    store = NotesStore(tmp_path, soft_limit=1, hard_limit=1)
    for topic, updated in (("aaa-new", "2026-11-03"), ("zzz-old", "2026-10-20")):
        path = store.note_path("work", topic)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"---\nname: {topic}\ndescription: {topic} summary\nupdated: {updated}\n---\nBODY\n",
            encoding="utf-8",
        )

    entries = store.list_entries()
    assert [entry.name for entry in entries] == ["aaa-new"]
    assert entries[0].updated == "2026-11-03"
    rendered = store.render_index()
    assert "aaa-new summary" in rendered
    assert "zzz-old summary" not in rendered
    assert "(-1 older notes" in rendered
