"""``approvals.mode: auto`` is a first-class mode on every surface."""

from unittest.mock import MagicMock

import pytest

import tools.approval as approval_module
from tools import auto_review as ar
from tools.approval import check_all_command_guards
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


@pytest.fixture
def auto_guards(monkeypatch):
    """Auto mode + interactive CLI + clean approval state and ruling stamp."""
    monkeypatch.setattr("tools.approval_context._get_approval_mode", lambda: "auto")
    monkeypatch.setenv("HERMES_INTERACTIVE", "1")
    for name in ("HERMES_YOLO_MODE", "HERMES_CRON_SESSION", "HERMES_GATEWAY_SESSION", "HERMES_EXEC_ASK"):
        monkeypatch.delenv(name, raising=False)
    approval_module._session_approved.clear()
    approval_module._pending.clear()
    approval_module._permanent_approved.clear()
    approval_module._permanent_approved_by_home.clear()
    ar._ruling_stamp.set(None)
    yield
    ar._ruling_stamp.set(None)
    approval_module._session_approved.clear()
    approval_module._pending.clear()
    approval_module._permanent_approved.clear()
    approval_module._permanent_approved_by_home.clear()


def test_auto_mode_keeps_the_human_gate_without_a_ruling(auto_guards):
    # Nothing ruled on this call (a user bang command, a skip_tools terminal call):
    # auto must not zero-review it — the dangerous-command prompt decides.
    cb = MagicMock(return_value="deny")
    result = check_all_command_guards("rm -rf /tmp/stuff", "local", approval_callback=cb)
    assert result["approved"] is False
    cb.assert_called_once()


def test_auto_mode_consumes_the_reviewers_ruling_instead_of_prompting(auto_guards):
    # The reviewer (or its human escalation) ruled on this exact call at dispatch:
    # the guard consumes the stamp and asks nobody twice.
    ar._stamp_ruling("terminal")
    cb = MagicMock(return_value="deny")
    result = check_all_command_guards("rm -rf /tmp/stuff", "local", approval_callback=cb)
    assert result["approved"] is True
    cb.assert_not_called()
