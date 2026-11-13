"""T-28: agent rewrites of notes/ keep the previous file under .versions/ (Android 记 05 parity)."""

from pathlib import Path

from tools.file_tools import patch_tool, read_file_tool, write_file_tool


def _versions(home: Path) -> list[Path]:
    root = home / ".versions"
    return sorted(root.iterdir()) if root.is_dir() else []


def test_write_file_archives_previous_note(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    note = tmp_path / "notes" / "work" / "plan.md"
    note.parent.mkdir(parents=True)
    note.write_text("---\nname: 计划\ndescription: 初版\n---\n旧正文\n", encoding="utf-8")

    read_file_tool(str(note))
    write_file_tool(str(note), "---\nname: 计划\ndescription: 新版\n---\n新正文\n")

    assert "新正文" in note.read_text(encoding="utf-8")
    archived = _versions(tmp_path)
    assert len(archived) == 1
    assert "旧正文" in archived[0].read_text(encoding="utf-8")
    assert archived[0].name.startswith("notes_work_plan.md.")


def test_patch_archives_previous_note_and_soul_is_never_archived(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    note = tmp_path / "notes" / "life" / "moving.md"
    note.parent.mkdir(parents=True)
    note.write_text("---\nname: 搬家\ndescription: 十一月\n---\n旧正文\n", encoding="utf-8")
    soul = tmp_path / "SOUL.md"
    soul.write_text("身份甲\n", encoding="utf-8")

    patch_tool(mode="replace", path=str(note), old_string="旧正文", new_string="新正文")
    read_file_tool(str(soul))
    write_file_tool(str(soul), "身份乙\n")

    assert "新正文" in note.read_text(encoding="utf-8")
    assert soul.read_text(encoding="utf-8") == "身份乙\n"
    archived = _versions(tmp_path)
    assert len(archived) == 1
    assert "旧正文" in archived[0].read_text(encoding="utf-8")
