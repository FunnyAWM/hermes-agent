"""Windows shell hint follows the command-sandbox gate: PowerShell when confined, git-bash when not.

The gate answer is fixed at system-prompt build time (the prompt must stay byte-stable), so these
tests pin the only branch that may change there: ``local_command_sandbox.active()``.
"""

import pytest

from agent import prompt_builder
from tools.environments import local_command_sandbox


@pytest.mark.platforms("windows")
def test_confined_windows_session_gets_the_powershell_sandbox_hint(monkeypatch):
    monkeypatch.setattr(local_command_sandbox, "active", lambda: True)

    hints = prompt_builder._local_host_hints()

    assert prompt_builder._WINDOWS_SANDBOX_SHELL_HINT in hints
    assert prompt_builder._WINDOWS_BASH_SHELL_HINT not in hints
    assert "PowerShell" in prompt_builder._WINDOWS_SANDBOX_SHELL_HINT
    assert "terminal.sandbox" not in hints[0]  # the hint, not the host block, carries the shell rule


@pytest.mark.platforms("windows")
def test_unconfined_windows_session_keeps_the_git_bash_hint(monkeypatch):
    monkeypatch.setattr(local_command_sandbox, "active", lambda: False)

    hints = prompt_builder._local_host_hints()

    assert prompt_builder._WINDOWS_BASH_SHELL_HINT in hints
    assert prompt_builder._WINDOWS_SANDBOX_SHELL_HINT not in hints


@pytest.mark.platforms("posix")
def test_posix_host_hint_is_unchanged_whatever_the_gate_says(monkeypatch):
    for active in (False, True):
        monkeypatch.setattr(local_command_sandbox, "active", lambda: active)
        hints = prompt_builder._local_host_hints()
        assert len(hints) == 1
        assert "Shell:" not in hints[0]
