"""A sealed install refuses in-place pm writes: the runtime upgrades by package.

`pm install <tool>` is exempt: it writes the writable store, which is how a
sealed install pulls missing tools after setup. Bare installs and
--extra/--test-environment sync the sealed venv, so they stay refused.
"""
from __future__ import annotations

import pytest

from pm import cli


@pytest.mark.parametrize("command", ["repair", "gc", "update", "lock"])
def test_sealed_install_refuses_write_commands(monkeypatch, capsys, command):
    monkeypatch.setattr(cli, "sealed", lambda: True)
    assert cli.main([command]) == 1
    out = capsys.readouterr().out
    assert "sealed" in out
    assert "install a newer package" in out


@pytest.mark.parametrize("argv", [[], ["--extra", "all"], ["--test-environment", "test"]])
def test_sealed_install_refuses_venv_syncing_forms(monkeypatch, capsys, argv):
    monkeypatch.setattr(cli, "sealed", lambda: True)
    assert cli.main(["install", *argv]) == 1
    out = capsys.readouterr().out
    assert "sealed" in out


@pytest.mark.parametrize("argv", [["ripgrep"], ["--tools-only"], ["git", "node"]])
def test_sealed_install_allows_writable_store_tools(monkeypatch, argv):
    monkeypatch.setattr(cli, "sealed", lambda: True)
    monkeypatch.setattr("pm.runtime.is_runtime", lambda: True)
    calls = []
    monkeypatch.setattr(cli, "cmd_install", lambda args: calls.append(args) or 0)
    assert cli.main(["install", *argv]) == 0
    assert len(calls) == 1


def test_unsealed_install_dispatches_normally(monkeypatch):
    monkeypatch.setattr(cli, "sealed", lambda: False)
    monkeypatch.setattr("pm.runtime.is_runtime", lambda: True)
    calls = []
    monkeypatch.setattr(cli, "cmd_install", lambda args: calls.append(args) or 0)
    assert cli.main(["install"]) == 0
    assert len(calls) == 1
