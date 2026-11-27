"""A linked worktree's common git dir must stay readable inside the terminal sandbox."""

import subprocess

from tools.environments.local_command_sandbox import SandboxShell, build_policy, powershell_script


def _git(*args):
    subprocess.run(["git", *args], check=True, capture_output=True, text=True)


def test_worktree_common_git_dir_is_a_read_grant(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("-C", str(repo), "init", "-q")
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    _git("-C", str(repo), "add", "README.md")
    _git("-C", str(repo), "-c", "user.name=Sandbox Test", "-c", "user.email=sandbox@localhost",
         "commit", "-q", "-m", "seed")
    worktree = tmp_path / "worktree"
    _git("-C", str(repo), "worktree", "add", "--detach", str(worktree), "HEAD")

    policy = build_policy(cwd=str(worktree), scratch=str(tmp_path / "scratch"),
                          shell=SandboxShell("posix", "/bin/sh"))
    read = policy["spec"]["fs"]["read"]
    assert any(str(repo / ".git") in entry for entry in read)

    script = powershell_script("git status", cwd=str(worktree), markers=None)
    assert "GIT_CONFIG_NOSYSTEM" in script and "GIT_CONFIG_GLOBAL" in script
