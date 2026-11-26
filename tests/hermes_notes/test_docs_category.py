"""PRD §7.1 / D8: ``notes/docs/`` is indexed but never a writable category."""

import pytest

from hermes_notes import CATEGORY_DISPLAY_NAMES, NotesStore


def _write(path, name, description="summary"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name}\ndescription: {description}\nupdated: 2026-11-06\n---\nBODY_ONLY\n",
        encoding="utf-8",
    )


def test_docs_directory_is_scanned_and_indexed(tmp_path):
    store = NotesStore(tmp_path)
    _write(tmp_path / "notes" / "docs" / "quote.md", "报价单", "合同金额与日期")

    entries = store.list_entries()
    assert [entry.category for entry in entries] == ["docs"]
    assert "文档/报价单 — 合同金额与日期" in store.render_index()


def test_docs_is_rejected_for_new_notes(tmp_path):
    store = NotesStore(tmp_path)
    with pytest.raises(ValueError, match="read-only"):
        store.note_path("docs", "new-import")


def test_unknown_directory_still_falls_back_to_other(tmp_path):
    store = NotesStore(tmp_path)
    _write(tmp_path / "notes" / "legacy" / "old.md", "旧笔记", "兼容目录")

    assert store.list_entries()[0].category == "other"
    assert "其他/旧笔记 — 兼容目录" in store.render_index()


def test_docs_display_name_is_registered():
    assert CATEGORY_DISPLAY_NAMES["docs"] == "文档"
