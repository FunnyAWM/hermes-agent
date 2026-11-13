"""D8: the model may read imported source text but never write notes/docs/."""

import json

from tools.file_tools import patch_tool, read_file_tool, write_file_tool


def _docs_file(tmp_path):
    path = tmp_path / "notes" / "docs" / "source.md"
    path.parent.mkdir(parents=True)
    path.write_text("ORIGINAL SOURCE\n", encoding="utf-8")
    return path


def test_write_file_rejects_docs_tree(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    path = _docs_file(tmp_path)

    result = json.loads(write_file_tool(str(path), "OVERWRITTEN\n"))

    assert "error" in result
    assert "read-only" in result["error"]
    assert path.read_text(encoding="utf-8") == "ORIGINAL SOURCE\n"


def test_patch_rejects_docs_tree(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    path = _docs_file(tmp_path)

    result = json.loads(patch_tool(mode="replace", path=str(path), old_string="ORIGINAL", new_string="CHANGED"))

    assert "error" in result
    assert "read-only" in result["error"]
    assert path.read_text(encoding="utf-8") == "ORIGINAL SOURCE\n"


def test_read_and_regular_notes_stay_writable(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    docs = _docs_file(tmp_path)
    note = tmp_path / "notes" / "work" / "plan.md"
    note.parent.mkdir(parents=True)

    assert "ORIGINAL SOURCE" in read_file_tool(str(docs))
    result = json.loads(write_file_tool(str(note), "PLAN\n"))
    assert "error" not in result and result.get("verified") is True
    assert note.read_text(encoding="utf-8") == "PLAN\n"
