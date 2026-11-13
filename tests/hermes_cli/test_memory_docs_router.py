"""Tests for ``hermes_cli.web_routers.memory_docs`` — the desktop Memory panel.

The panel lists the four context documents (existing or not), indexes the notes tree,
reads ``notes/**.md``, and saves any of them except imported source under ``notes/docs/``.
Paths resolve inside the profile's HERMES_HOME, so ``_home`` is the single seam these
tests replace.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from hermes_cli.web_routers import memory_docs


def _call(fn, *args, **kwargs):
    return asyncio.run(fn(*args, **kwargs))


def test_docs_report_the_four_context_files(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_docs, "_home", lambda: tmp_path)
    (tmp_path / "SOUL.md").write_text("soul text", encoding="utf-8")
    (tmp_path / "memories").mkdir()
    (tmp_path / "memories" / "USER.md").write_text("user text", encoding="utf-8")

    payload = _call(memory_docs.memory_docs)

    files = {entry["id"]: entry for entry in payload["files"]}
    assert list(files) == ["soul", "agents", "user", "memory"]
    assert files["soul"]["exists"] is True and files["soul"]["content"] == "soul text"
    assert files["user"]["content"] == "user text"
    # AGENTS.md and MEMORY.md simply are not there yet — listed, flagged, not an error.
    assert files["agents"]["exists"] is False and files["agents"]["content"] == ""
    assert files["memory"]["exists"] is False


def test_docs_index_the_notes_tree(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_docs, "_home", lambda: tmp_path)
    (tmp_path / "notes" / "deep").mkdir(parents=True)
    (tmp_path / "notes" / "a.md").write_text("a", encoding="utf-8")
    (tmp_path / "notes" / "deep" / "b.md").write_text("b", encoding="utf-8")
    (tmp_path / "notes" / "ignored.txt").write_text("nope", encoding="utf-8")

    payload = _call(memory_docs.memory_docs)

    assert [note["path"] for note in payload["notes"]] == ["notes/a.md", "notes/deep/b.md"]


def test_note_reads_only_notes_markdown(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_docs, "_home", lambda: tmp_path)
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "a.md").write_text("hello note", encoding="utf-8")
    (tmp_path / "SOUL.md").write_text("secret", encoding="utf-8")

    payload = _call(memory_docs.memory_note, path="notes/a.md")
    assert payload["content"] == "hello note" and payload["path"] == "notes/a.md"

    for escape in ("SOUL.md", "../SOUL.md", "notes/../../SOUL.md"):
        with pytest.raises(HTTPException) as excinfo:
            _call(memory_docs.memory_note, path=escape)
        assert excinfo.value.status_code == 400


def test_writes_a_context_document_atomically(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_docs, "_home", lambda: tmp_path)

    payload = _call(
        memory_docs.memory_write,
        memory_docs.MemoryFileWrite(path="SOUL.md", content="# Soul\nnew line\n"),
    )

    assert payload["ok"] is True and payload["path"] == "SOUL.md"
    assert (tmp_path / "SOUL.md").read_text(encoding="utf-8") == "# Soul\nnew line\n"
    # One rename, no scratch file left behind.
    assert not [entry.name for entry in tmp_path.iterdir() if entry.name.endswith(".tmp")]


def test_creates_a_missing_document_and_note_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_docs, "_home", lambda: tmp_path)

    _call(memory_docs.memory_write, memory_docs.MemoryFileWrite(path="AGENTS.md", content="rules"))
    _call(memory_docs.memory_write, memory_docs.MemoryFileWrite(path="notes/work/trip.md", content="osaka"))

    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == "rules"
    assert (tmp_path / "notes" / "work" / "trip.md").read_text(encoding="utf-8") == "osaka"


def test_refuses_paths_outside_the_whitelist(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_docs, "_home", lambda: tmp_path)
    (tmp_path / "notes" / "docs").mkdir(parents=True)

    for bad in (
        "../outside.md",
        "/etc/passwd.md",
        "config.yaml",
        "notes/../../SOUL.md",
        "notes/docs/import.md",
        "notes/../memories/USER.md",
    ):
        with pytest.raises(HTTPException) as excinfo:
            _call(memory_docs.memory_write, memory_docs.MemoryFileWrite(path=bad, content="x"))
        assert excinfo.value.status_code == 400
    assert not (tmp_path / "notes" / "docs" / "import.md").exists()


def test_rejects_an_oversized_write(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_docs, "_home", lambda: tmp_path)

    with pytest.raises(HTTPException) as excinfo:
        _call(
            memory_docs.memory_write,
            memory_docs.MemoryFileWrite(path="notes/big.md", content="x" * (memory_docs._MAX_NOTE_BYTES + 1)),
        )
    assert excinfo.value.status_code == 413
    assert not (tmp_path / "notes" / "big.md").exists()


def test_docs_mark_imported_documents_read_only(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_docs, "_home", lambda: tmp_path)
    (tmp_path / "notes" / "docs").mkdir(parents=True)
    (tmp_path / "notes" / "docs" / "import.md").write_text("src", encoding="utf-8")
    (tmp_path / "notes" / "work" / "a.md").parent.mkdir(parents=True)
    (tmp_path / "notes" / "work" / "a.md").write_text("a", encoding="utf-8")

    payload = _call(memory_docs.memory_docs)

    editable = {note["path"]: note["editable"] for note in payload["notes"]}
    assert editable == {"notes/docs/import.md": False, "notes/work/a.md": True}
    assert all(entry["editable"] for entry in payload["files"])
