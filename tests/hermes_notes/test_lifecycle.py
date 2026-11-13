"""Android 设 13 parity: soft-limit expiry cleanup, desktop-provenance usage marks, archive-first deletes."""

from datetime import date, timedelta

import pytest

from hermes_notes import NotesStore
from hermes_notes import lifecycle


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _note(name, *, expires=None, last_used=None, marker=None, body="正文"):
    lines = ["---", f"name: {name}", "description: 说明", "updated: 2026-11-01"]
    if expires:
        lines.append(f"expires: {expires}")
    if last_used is not None:
        lines.append(f"lastUsed: {last_used}")
    if marker:
        lines.append(f"lastUsedBy: {marker}")
    lines += ["---", "", body]
    return "\n".join(lines)


def test_default_soft_limit_is_128_and_hard_cap_truncates_explicitly(tmp_path):
    store = NotesStore(tmp_path)
    assert store.soft_limit == 128
    assert store.hard_limit is None
    for index in range(3):
        _write(store.note_path("work", f"n{index}"), _note(f"n{index}"))
    assert len(store.list_entries()) == 3
    assert "hard cap" not in store.render_index()

    capped = NotesStore(tmp_path, soft_limit=2, hard_limit=2)
    assert len(capped.list_entries()) == 2
    assert "hard cap" in capped.render_index()
    with pytest.raises(ValueError):
        NotesStore(tmp_path, soft_limit=5, hard_limit=4)
    with pytest.raises(ValueError):
        NotesStore(tmp_path, soft_limit=0)


def test_expired_notes_are_removed_only_above_the_soft_limit(tmp_path):
    store = NotesStore(tmp_path)
    expired = _write(store.note_path("life", "old"), _note("旧", expires="2026-01-01"))
    kept = _write(store.note_path("life", "new"), _note("新", expires="2027-01-01"))
    today = date(2026, 11, 10)

    assert lifecycle.remove_expired_notes(tmp_path, soft_limit=5, today=today) == 0
    assert expired.exists() and kept.exists()

    assert lifecycle.remove_expired_notes(tmp_path, soft_limit=1, today=today) == 1
    assert not expired.exists()
    assert kept.exists()
    archives = list((tmp_path / ".versions").iterdir())
    assert len(archives) == 1
    assert "旧" in archives[0].read_text(encoding="utf-8")


def test_ambiguous_expiry_and_failed_archive_never_delete(tmp_path, monkeypatch):
    store = NotesStore(tmp_path)
    duplicate = _write(store.note_path("life", "dup"), "---\nname: 重复\nexpires: 2020-01-01\n"
                       "expires: 2020-01-02\n---\n正文\n")
    malformed = _write(store.note_path("life", "bad"), _note("坏", expires="2020-1-1"))
    in_body = _write(store.note_path("life", "body"), "---\nname: 正文\n---\nexpires: 2020-01-01\n")
    archived = _write(store.note_path("life", "archive"), _note("留档", expires="2020-01-01"))

    def failing_archive(home, path):
        raise OSError("disk full")

    monkeypatch.setattr(lifecycle, "archive_note", failing_archive)
    removed = lifecycle.remove_expired_notes(tmp_path, soft_limit=1, today=date(2026, 11, 10))

    assert removed == 0
    for note in (duplicate, malformed, in_body, archived):
        assert note.exists()


def test_disabled_cleanup_and_unused_turns_zero_are_noops(tmp_path):
    store = NotesStore(tmp_path)
    expired = _write(store.note_path("life", "old"), _note("旧", expires="2020-01-01"))
    assert lifecycle.remove_expired_notes(tmp_path, soft_limit=1, enabled=False) == 0
    assert expired.exists()

    docs = _write(tmp_path / "notes" / "docs" / "a.md", _note("甲", last_used=1, marker="desktop"))
    assert lifecycle.stale_documents(tmp_path, turn=99, unused_turns=0) == []
    assert docs.exists()


def test_mark_documents_used_stamps_turn_and_keeps_body(tmp_path):
    docs = _write(tmp_path / "notes" / "docs" / "spec.md", _note("规范", body="原始正文"))
    note = _write(tmp_path / "notes" / "work" / "plan.md", _note("计划"))

    assert lifecycle.mark_documents_used(tmp_path, ["notes/docs/spec.md", "notes/work/plan.md"], 4) == 1
    text = docs.read_text(encoding="utf-8")
    assert "lastUsed: 4" in text
    assert "lastUsedBy: desktop" in text
    assert "name: 规范" in text and "updated: 2026-11-01" in text
    assert text.endswith("原始正文")
    assert "lastUsed" not in note.read_text(encoding="utf-8")

    # Same turn again: nothing to rewrite.
    assert lifecycle.mark_documents_used(tmp_path, [docs], 4) == 0


def test_stale_documents_requires_desktop_provenance_and_threshold(tmp_path):
    docs = tmp_path / "notes" / "docs"
    old = _write(docs / "old.md", _note("旧文", last_used=1, marker="desktop"))
    recent = _write(docs / "recent.md", _note("新文", last_used=8, marker="desktop"))
    synced = _write(docs / "synced.md", _note("同步", last_used=1))
    unknown = _write(docs / "unknown.md", _note("未知"))

    stale = lifecycle.stale_documents(tmp_path, turn=11, unused_turns=10)
    assert [item.path for item in stale] == ["notes/docs/old.md"]
    assert stale[0].unused_turns == 10
    assert old.exists() and recent.exists() and synced.exists() and unknown.exists()


def test_delete_documents_archives_before_removing(tmp_path):
    docs = tmp_path / "notes" / "docs" / "gone.md"
    _write(docs, _note("删", body="原文"))
    kept = _write(tmp_path / "notes" / "work" / "plan.md", _note("计划"))

    assert lifecycle.delete_documents(tmp_path, ["notes/docs/gone.md", "notes/work/plan.md"]) == 1
    assert not docs.exists()
    assert kept.exists()
    archived = list((tmp_path / ".versions").iterdir())
    assert len(archived) == 1
    assert "原文" in archived[0].read_text(encoding="utf-8")


def test_failed_archive_keeps_document(tmp_path, monkeypatch):
    docs = _write(tmp_path / "notes" / "docs" / "keep.md", _note("保留"))

    def failing_archive(home, path):
        raise OSError("read-only")

    monkeypatch.setattr(lifecycle, "archive_note", failing_archive)
    removed = lifecycle.delete_documents(tmp_path, ["notes/docs/keep.md"])

    assert removed == 0
    assert docs.exists()


def test_archive_before_overwrite_covers_only_the_model_writable_surface(tmp_path):
    user = _write(tmp_path / "memories" / "USER.md", "旧用户\n")
    note = _write(tmp_path / "notes" / "work" / "plan.md", "旧笔记\n")
    soul = _write(tmp_path / "SOUL.md", "身份\n")

    for path in (user, note, soul):
        lifecycle.archive_before_overwrite(tmp_path, path)

    archives = sorted(path.name for path in (tmp_path / ".versions").iterdir())
    assert any(name.startswith("memories_USER.md.") for name in archives)
    assert any(name.startswith("notes_work_plan.md.") for name in archives)
    assert not any("SOUL" in name for name in archives)
    assert lifecycle.archive_before_overwrite(tmp_path, tmp_path / "notes" / "work" / "none.md") is None


def test_expiration_and_turn_parsers_require_strict_unique_header_values():
    today = date(2026, 11, 10)
    assert lifecycle.expiration_date(_note("a", expires=(today - timedelta(days=1)).isoformat())) == (
        today - timedelta(days=1))
    assert lifecycle.expiration_date(_note("a", expires="2026-13-40")) is None
    assert lifecycle.expiration_date("正文里的 expires: 2020-01-01\n") is None
    assert lifecycle.last_used_turn(_note("a", last_used=7)) == 7
    assert lifecycle.last_used_turn(_note("a", last_used="x7")) is None
    assert lifecycle.last_used_turn("---\nlastUsed: 1\nlastUsed: 2\n---\n") is None
