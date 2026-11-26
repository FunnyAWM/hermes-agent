"""T-07: the offline upstream-upgrade preflight reports what blocks a rebase."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check_upstream_upgrade.py"

_GIT = ("git", "-c", "user.name=Upgrade Test", "-c", "user.email=upgrade@test.invalid",
        "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false")


def _load():
    spec = importlib.util.spec_from_file_location("upstream_upgrade_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


preflight = _load()


def _git(repo: Path, *args: str) -> None:
    subprocess.run([*_GIT, *args], cwd=repo, check=True, capture_output=True, text=True)


def _write_release(repo: Path, *, py: str, js: str) -> None:
    (repo / "pyproject.toml").write_text(
        f'[project]\nname = "hermes-agent"\nversion = "{py}"\n', encoding="utf-8")
    package = repo / "apps" / "desktop" / "package.json"
    package.parent.mkdir(parents=True, exist_ok=True)
    package.write_text(json.dumps({"name": "hermes-desktop", "version": js}), encoding="utf-8")


def _write_checklist(repo: Path) -> None:
    (repo / "UPSTREAM_UPGRADE_CHECKLIST.md").write_text(
        "## 1 准入\n## 2 Rebase\n## 3 记忆兼容性\n## 4 门禁\n"
        "run `scripts/check` and `check_upstream_memory.py`\n", encoding="utf-8")


def _repo(tmp_path: Path, *, tag: str | None = None,
          py: str = "0.22.5+fork1", js: str = "0.22.5-fork.1") -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "remote", "add", "origin", "https://github.com/FunnyAWM/hermes-agent.git")
    _git(repo, "remote", "add", "upstream", "https://github.com/nousresearch/hermes-agent.git")
    _write_release(repo, py=py, js=js)
    _write_checklist(repo)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    if tag:
        _git(repo, "tag", "-a", tag, "-m", tag)
    return repo


def _check_names(report: dict) -> dict[str, dict]:
    return {check["name"]: check for check in report["checks"]}


def test_this_checkout_is_honestly_blocked():
    report = preflight.build_report()
    checks = _check_names(report)
    assert report["ok"] is False
    assert checks["upstream_remote"]["ok"] is False
    assert checks["candidate_tag"]["ok"] is False
    assert checks["declarations"]["ok"] is True
    assert checks["checklist"]["ok"] is True
    assert checks["memory_contract"]["ok"] is True
    assert "upstream_remote" in report["blocked_reason"]
    assert report["network"] == "not contacted (fetch disabled by this preflight)"


def test_fork_and_prerelease_tags_are_rejected(tmp_path):
    repo = _repo(tmp_path)
    for tag in ("v0.22.5-fork.1", "v0.23.0-rc.1", "0.23.0"):
        report = preflight.build_report(repo, tag)
        assert _check_names(report)["candidate_tag"]["ok"] is False, tag
        assert "not an upstream stable tag" in _check_names(report)["candidate_tag"]["detail"]


def test_stable_tag_missing_locally_blocks(tmp_path):
    repo = _repo(tmp_path)
    report = preflight.build_report(repo, "v0.23.0")
    assert _check_names(report)["candidate_tag"]["ok"] is False
    assert "not present locally" in _check_names(report)["candidate_tag"]["detail"]


def test_ready_repo_reports_next_versions(tmp_path):
    repo = _repo(tmp_path, tag="v0.23.0", py="0.23.0+fork1", js="0.23.0-fork.1")
    report = preflight.build_report(repo, "v0.23.0")
    checks = _check_names(report)
    assert report["ok"] is True, report["blocked_reason"]
    assert checks["candidate_tag"]["ok"] is True
    # Same base already declared at fork.1 -> a rebuild would be fork.2.
    assert report["candidate"]["expected_next"]["display"] == "0.23.0-fork.2"
    assert report["candidate"]["expected_next"]["pep440"] == "0.23.0+fork2"


def test_new_base_resets_the_fork_counter(tmp_path):
    repo = _repo(tmp_path, tag="v0.23.0")
    report = preflight.build_report(repo, "v0.23.0")
    # Current tree is still 0.22.5; the candidate base differs, so the rewrite starts at fork.1.
    assert report["candidate"]["expected_next"]["tag"] == "v0.23.0-fork.1"
    assert report["candidate"]["expected_next"]["pep440"] == "0.23.0+fork1"


def test_cli_json_blocked_on_a_clean_but_unconfigured_repo(tmp_path, capsys):
    repo = _repo(tmp_path)
    code = preflight.main(["--repo", str(repo), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["ok"] is False
    assert payload["blocked_reason"]
