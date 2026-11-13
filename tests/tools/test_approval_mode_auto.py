"""``approvals.mode: auto`` is a first-class mode on every surface."""

from tools.approval_context import _normalize_approval_mode


def test_normalizer_accepts_auto():
    assert _normalize_approval_mode("auto") == "auto"
    assert _normalize_approval_mode("  Auto  ") == "auto"


def test_slash_command_persists_auto(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from hermes_cli.approval_mode import run_approval_mode_command

    result = run_approval_mode_command("auto")

    assert result.ok is True
    assert result.mode == "auto"
    assert result.changed is True
    assert (tmp_path / "config.yaml").read_text(encoding="utf-8").count("auto") == 1


def test_slash_command_still_rejects_unknown_modes(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from hermes_cli.approval_mode import run_approval_mode_command

    result = run_approval_mode_command("yolo")

    assert result.ok is False
    assert "auto" in result.message
