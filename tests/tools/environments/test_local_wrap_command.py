"""The LocalEnvironment override keeps the base command contract used by the terminal tool."""

from tools.environments.local import LocalEnvironment


def test_wrap_command_accepts_cwd_fallback(tmp_path):
    env = LocalEnvironment(cwd=str(tmp_path))
    script = env._wrap_command("echo hello", str(tmp_path), cwd_fallback=True)
    assert "echo hello" in script
    assert env._cwd_fallback_marker in script
