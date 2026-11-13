"""Separate bootstrap stages reuse compatible local Git without downloading."""

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


INSTALLER = Path(__file__).resolve().parents[3] / "scripts" / "install.ps1"


@pytest.mark.platforms("windows")
def test_stage_processes_reuse_local_git_without_downloading(tmp_path):
    powershell = shutil.which("powershell.exe")
    system_git = shutil.which("git")
    assert powershell and system_git

    origin = tmp_path / "origin"
    origin.mkdir()
    subprocess.run([system_git, "-C", str(origin), "init", "-q", "-b", "main"], check=True)
    (origin / "README").write_text("fixture", encoding="utf-8")
    subprocess.run([system_git, "-C", str(origin), "add", "README"], check=True)
    subprocess.run([system_git, "-C", str(origin), "-c", "user.name=Smoke",
                    "-c", "user.email=smoke@example.invalid", "commit", "-qm", "initial"], check=True)
    expected = subprocess.run([system_git, "-C", str(origin), "rev-parse", "HEAD"],
                              check=True, capture_output=True, text=True).stdout.strip()

    home = tmp_path / "home"
    store = tmp_path / "tools"
    env = dict(os.environ, HERMES_HOME=str(home), HERMES_RUNTIME_DIR=str(store),
               HERMES_REPO_URL=str(origin))

    def stage(name):
        result = subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                 "-File", str(INSTALLER), "-Stage", name, "-Json"],
                                env=env, capture_output=True, text=True, timeout=240, check=False)
        frames = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
        assert result.returncode == 0 and len(frames) == 1 and frames[0]["ok"], result.stdout + result.stderr

    stage("prerequisites")
    assert not list(store.glob("git-*"))
    # A broken earlier candidate must not hide the usable local installation.
    poison = tmp_path / "poison"
    poison.mkdir()
    (poison / "git.cmd").write_text("@echo unpinned git invoked 1>&2 & exit /b 73\r\n", encoding="utf-8")
    env["PATH"] = str(poison) + os.pathsep + env["PATH"]
    stage("repository")  # new process; prerequisites' PATH cannot propagate
    stage("complete")  # the marker's bare git call is also a new process
    checkout = home / "hermes-agent"
    actual = subprocess.run([system_git, "-C", str(checkout), "rev-parse", "HEAD"],
                            check=True, capture_output=True, text=True).stdout.strip()
    marker = json.loads((checkout / ".hermes-bootstrap-complete").read_text(encoding="utf-8-sig"))
    assert actual == expected == marker["pinnedCommit"]

    assert not list(store.glob("git-*"))
    # Local Git needs no downloadable artifact for this target.
    probe = (f". '{INSTALLER}'; "
             "$script:GitPinFiles.Remove(('win32-' + (Get-WindowsArch))); "
             "if (Ensure-Git) { exit 0 } else { exit 1 }")
    refused = subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                              "-Command", probe], env=env, capture_output=True, text=True, timeout=240, check=False)
    assert refused.returncode == 0, refused.stdout + refused.stderr
