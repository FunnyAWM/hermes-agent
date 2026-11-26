"""Full Access profile and the one-command sandbox escalation.

The model may name a wider level for a single command (``terminal(..., sandbox=...)``);
anything wider than the active profile goes through the human-approval gate, and the
configured profile applies again to the next call.
"""

from pathlib import Path

import pytest

from tools import terminal_tool
from tools.environments import local_command_sandbox as gate


@pytest.fixture()
def hermes_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


def _write_config(home: Path, body: str) -> None:
    (home / "config.yaml").write_text(body, encoding="utf-8")


READ_ONLY_CONFIG = (
    "terminal:\n  sandbox:\n    enabled: true\n    profile: read-only\n"
    "    profiles:\n      read-only:\n        preset: read-only\n"
)


def test_full_access_profile_leaves_the_gate_off(hermes_home):
    _write_config(
        hermes_home,
        "terminal:\n  sandbox:\n    enabled: true\n    profile: full-access\n"
        "    profiles:\n      full-access:\n        preset: full-access\n",
    )
    assert gate.settings().enabled is True
    assert gate.active() is False
    assert gate.file_writes_read_only() is False


def test_blocked_command_tells_the_model_how_to_ask_for_a_wider_level(hermes_home):
    """A blocked run must name the escalation mechanism, not only the config file: the model
    can widen ONE command, and without that sentence it retries at a tighter level instead."""
    note = gate.failure_note(gate.BLOCKED_EXIT, "denied by policy")
    assert 'sandbox="full-access"' in note
    assert "approve" in note
    assert "config.yaml" in note  # the user-facing widening path stays available


def test_escalation_is_scoped_to_one_command(hermes_home):
    _write_config(hermes_home, READ_ONLY_CONFIG)
    assert gate.active_profile().preset == gate.READ_ONLY
    with gate.escalation(gate.FULL_ACCESS):
        assert gate.active_profile().preset == gate.FULL_ACCESS
        assert gate.active() is False
    assert gate.active_profile().preset == gate.READ_ONLY
    assert gate.active() is True


def test_widening_only_moves_right(hermes_home):
    _write_config(hermes_home, READ_ONLY_CONFIG)
    assert gate.widening(gate.WORKSPACE_WRITE) is True
    assert gate.widening(gate.READ_ONLY) is False
    with gate.escalation(gate.WORKSPACE_EXEC):
        assert gate.widening(gate.WORKSPACE_WRITE) is False
        assert gate.widening(gate.FULL_ACCESS) is True


def test_wider_level_needs_the_users_approval(hermes_home, monkeypatch):
    _write_config(hermes_home, READ_ONLY_CONFIG)
    calls = []

    def _deny(tool_name, reason, **kwargs):
        calls.append(reason)
        return {"approved": False, "message": "The user declined."}

    monkeypatch.setattr("tools.approval.request_tool_approval", _deny)
    with pytest.raises(terminal_tool._Rejected) as rejected:
        terminal_tool._sandbox_escalation_scope(gate.WORKSPACE_EXEC, "local", "npm ci")
    assert calls and "workspace-exec" in calls[0]
    assert "declined" in rejected.value.result_json


def test_approved_escalation_applies_to_the_scope(hermes_home, monkeypatch):
    _write_config(hermes_home, READ_ONLY_CONFIG)
    monkeypatch.setattr(
        "tools.approval.request_tool_approval",
        lambda *a, **k: {"approved": True},
    )
    with terminal_tool._sandbox_escalation_scope(gate.WORKSPACE_EXEC, "local", "npm ci"):
        assert gate.active_profile().preset == gate.WORKSPACE_EXEC
    assert gate.active_profile().preset == gate.READ_ONLY


def test_auto_mode_leaves_the_escalation_to_the_automatic_reviewer(hermes_home, monkeypatch):
    """approvals.mode: auto — the reviewer already ruled on this call; ask nobody twice."""
    _write_config(
        hermes_home,
        READ_ONLY_CONFIG + "approvals:\n  mode: auto\n",
    )

    def _unexpected(*args, **kwargs):
        raise AssertionError("auto mode must not open the human gate for an escalation")

    monkeypatch.setattr("tools.approval.request_tool_approval", _unexpected)
    with terminal_tool._sandbox_escalation_scope(gate.FULL_ACCESS, "local", "npm ci"):
        assert gate.active_profile().preset == gate.FULL_ACCESS


def test_auto_mode_keeps_the_human_gate_when_terminal_is_not_reviewed(hermes_home, monkeypatch):
    """terminal in skip_tools means nothing ruled on the call, so a human still decides."""
    _write_config(
        hermes_home,
        READ_ONLY_CONFIG + "approvals:\n  mode: auto\n  auto_review:\n    skip_tools: [terminal]\n",
    )
    calls = []
    monkeypatch.setattr(
        "tools.approval.request_tool_approval",
        lambda tool_name, reason, **kwargs: (calls.append(reason), {"approved": False})[1],
    )
    with pytest.raises(terminal_tool._Rejected):
        terminal_tool._sandbox_escalation_scope(gate.FULL_ACCESS, "local", "npm ci")
    assert calls


def test_tighter_level_runs_without_asking(hermes_home, monkeypatch):
    _write_config(
        hermes_home,
        "terminal:\n  sandbox:\n    enabled: true\n    profile: workspace-exec\n"
        "    profiles:\n      workspace-exec:\n        preset: workspace-exec\n      read-only:\n        preset: read-only\n",
    )

    def _unexpected(*args, **kwargs):
        raise AssertionError("a tighter level must not ask for approval")

    monkeypatch.setattr("tools.approval.request_tool_approval", _unexpected)
    with terminal_tool._sandbox_escalation_scope(gate.READ_ONLY, "local", "cat file"):
        assert gate.active_profile().preset == gate.READ_ONLY


def test_escalation_can_be_turned_off(hermes_home, monkeypatch):
    _write_config(hermes_home, READ_ONLY_CONFIG + "    escalation: false\n")

    def _unexpected(*args, **kwargs):
        raise AssertionError("escalation is disabled; the gate must not ask")

    monkeypatch.setattr("tools.approval.request_tool_approval", _unexpected)
    with pytest.raises(terminal_tool._Rejected) as rejected:
        terminal_tool._sandbox_escalation_scope(gate.WORKSPACE_WRITE, "local", "npm ci")
    assert "escalation" in rejected.value.result_json.lower()


def test_unknown_level_and_remote_backend_are_refused(hermes_home):
    _write_config(hermes_home, READ_ONLY_CONFIG)
    with pytest.raises(terminal_tool._Rejected):
        terminal_tool._sandbox_escalation_scope("root", "local", "id")
    with pytest.raises(terminal_tool._Rejected):
        terminal_tool._sandbox_escalation_scope(gate.WORKSPACE_WRITE, "docker", "id")
