"""Detached execution must not bypass the command sandbox gate.

``terminal(background=true)`` reaches ProcessRegistry.spawn_local, which Popens a host
shell directly, while ``execute_code``'s local session kernel is a host Python process.
Both leave LocalEnvironment.execute, where the sandctl wrap sits. Under an active gate a
background spawn therefore tracks `sandctl run` itself (killing the session kills the
sandbox — measured) and ``execute_code`` refuses, since its kernel cannot be wrapped that
way. Remote backends have their own isolation and keep working.
"""

import json
import os
from unittest.mock import MagicMock

from tools import terminal_tool_background
from tools.environments import local_command_sandbox


def test_background_spawn_runs_inside_the_sandbox(monkeypatch, tmp_path):
    monkeypatch.setattr(local_command_sandbox, "active", lambda: True)
    monkeypatch.setattr(
        local_command_sandbox, "settings",
        lambda: local_command_sandbox.SandboxSettings(True, "sandctl", "l1", True))
    monkeypatch.setattr(local_command_sandbox, "resolve_binary", lambda cfg: "sandctl")
    monkeypatch.setattr(
        local_command_sandbox, "confined_shell",
        lambda: local_command_sandbox.SandboxShell("posix", "/bin/bash"))
    monkeypatch.setattr("tools.environments.base.get_sandbox_dir", lambda: tmp_path / "sandboxes")
    registry = MagicMock()
    env = MagicMock()
    env.get_temp_dir.return_value = str(tmp_path / "scratch")

    session = terminal_tool_background._spawn(
        registry, env=env, env_type="local", command="echo hi", cwd=str(tmp_path),
        effective_task_id="t", task_id="t", session_key="", effective_pty=False,
    )

    spawned = registry.spawn_local.call_args.kwargs["command"]
    assert spawned.startswith("sandctl run"), spawned
    assert "/bin/bash" in spawned and "echo hi" in spawned
    # The session lists the user's command, not the launcher argv.
    assert session.command == "echo hi"
    registry.spawn_via_env.assert_not_called()


def test_background_spawn_remote_env_not_gated(monkeypatch):
    monkeypatch.setattr(local_command_sandbox, "active", lambda: True)
    registry = MagicMock()
    terminal_tool_background._spawn(
        registry, env=MagicMock(), env_type="docker", command="echo hi", cwd=os.getcwd(),
        effective_task_id="t", task_id="t", session_key="", effective_pty=False,
    )
    registry.spawn_via_env.assert_called_once()


def _patch_execute_code_entry(monkeypatch, env_type: str) -> None:
    from tools import process_registry, terminal_tool

    monkeypatch.setattr(process_registry, "_is_supervised_gateway_process", lambda: False)
    monkeypatch.setattr(terminal_tool, "_get_env_config", lambda: {"env_type": env_type})
    monkeypatch.setattr(terminal_tool, "_docker_has_host_access", lambda cfg: False)
    monkeypatch.setattr(
        "tools.approval.check_execute_code_guard",
        lambda code, env_type, has_host_access=False: {"approved": True},
    )


def test_execute_code_refused_under_sandbox_gate(monkeypatch):
    from tools.code_execution_tool import execute_code

    _patch_execute_code_entry(monkeypatch, "local")
    monkeypatch.setattr(local_command_sandbox, "active", lambda: True)
    result = json.loads(execute_code(code="print(1)"))
    assert "sandbox boundary" in result["error"]


def test_execute_code_remote_backend_not_refused_by_local_gate(monkeypatch):
    from tools import code_execution_tool

    _patch_execute_code_entry(monkeypatch, "docker")
    monkeypatch.setattr(local_command_sandbox, "active", lambda: True)
    monkeypatch.setattr(code_execution_tool, "_execute_remote", lambda *a, **k: '{"output": "remote"}')
    assert json.loads(code_execution_tool.execute_code(code="print(1)"))["output"] == "remote"
