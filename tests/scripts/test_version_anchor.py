"""Tests for the release-version anchor check (T-06) on throwaway git repositories."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "verify_version_anchor.py"

_GIT = ("git", "-c", "user.name=Version Test", "-c", "user.email=version@test.invalid",
        "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false")


def _load():
    spec = importlib.util.spec_from_file_location("version_anchor_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


anchor = _load()


def _git(repo: Path, *args: str) -> None:
    subprocess.run([*_GIT, *args], cwd=repo, check=True, capture_output=True, text=True)


def _write(repo: Path, *, py: str = "0.22.5+fork1", js: str = "0.22.5-fork.1") -> None:
    (repo / "pyproject.toml").write_text(
        f'[project]\nname = "hermes-agent"\nversion = "{py}"\n', encoding="utf-8")
    package = repo / "apps" / "desktop" / "package.json"
    package.parent.mkdir(parents=True, exist_ok=True)
    package.write_text(json.dumps({"name": "hermes-desktop", "version": js}), encoding="utf-8")


def _release_repo(tmp_path: Path, *, tag: str | None = "v0.22.5-fork.1") -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _write(repo)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "release")
    if tag:
        _git(repo, "tag", "-a", tag, "-m", tag)
    return repo


def test_matching_anchor_passes(tmp_path):
    report = anchor.check(_release_repo(tmp_path))
    assert report["ok"], report["reasons"]
    assert report["expected_tag"] == "v0.22.5-fork.1"
    assert report["describe"] == "v0.22.5-fork.1"


def test_declaration_mismatch_fails(tmp_path):
    repo = _release_repo(tmp_path)
    _write(repo, py="0.22.5+fork1", js="0.22.5-fork.2")
    report = anchor.check(repo)
    assert not report["ok"]
    assert any("declarations disagree" in reason for reason in report["reasons"])


def test_non_canonical_spelling_fails(tmp_path):
    repo = _release_repo(tmp_path, tag=None)
    _write(repo, py="0.22.5+fork.1")
    report = anchor.check(repo)
    assert not report["ok"]
    assert any("canonical PEP 440" in reason for reason in report["reasons"])


def test_commit_after_tag_fails_unless_allowed(tmp_path):
    repo = _release_repo(tmp_path)
    (repo / "notes.txt").write_text("later work\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "later")

    strict = anchor.check(repo)
    assert not strict["ok"]
    assert any("not exactly at" in reason for reason in strict["reasons"])
    lenient = anchor.check(repo, allow_distance=True)
    assert lenient["ok"], lenient["reasons"]
    assert "warning" in lenient


def test_checked_revision_can_predate_head(tmp_path):
    repo = _release_repo(tmp_path)
    tagged = subprocess.run([*_GIT, "rev-parse", "HEAD"], cwd=repo, check=True,
                            capture_output=True, text=True).stdout.strip()
    (repo / "notes.txt").write_text("later work\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "later")

    assert not anchor.check(repo)["ok"]
    report = anchor.check(repo, rev=tagged)
    assert report["ok"], report["reasons"]
    assert report["describe"] == "v0.22.5-fork.1"


def test_missing_tag_reported(tmp_path):
    report = anchor.check(_release_repo(tmp_path, tag=None))
    assert not report["ok"]
    assert any("no reachable tag" in reason for reason in report["reasons"])


def test_cli_json_reports_failure(tmp_path, capsys):
    repo = _release_repo(tmp_path)
    _write(repo, js="0.22.5-fork.9")
    code = anchor.main(["--repo", str(repo), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["ok"] is False and payload["reasons"]
