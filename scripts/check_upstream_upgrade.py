#!/usr/bin/env python3
"""Preflight an upstream stable-tag upgrade without touching the network.

Read-only: inspects the configured remotes, the candidate tag, both version declarations, the
checklist and the shared memory contract. It never fetches, edits, or rebases. ``--json`` emits a
machine-readable report with ``blocked_reason`` (``null`` when nothing blocks).

Usage::

    python scripts/check_upstream_upgrade.py --tag v0.23.0
    python scripts/check_upstream_upgrade.py --json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import fork_version
import memory_contract
import verify_version_anchor

CHECKLIST = "UPSTREAM_UPGRADE_CHECKLIST.md"
EXPECTED_UPSTREAM = "nousresearch/hermes-agent"
CHECKLIST_ANCHORS = ("## 1", "## 2", "## 3", "## 4", "scripts/check", "check_upstream_memory.py")


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=60, check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _remotes(repo: Path) -> dict[str, str]:
    remotes: dict[str, str] = {}
    for line in _git(repo, "remote", "-v").splitlines():
        parts = line.split()
        if len(parts) >= 2:
            remotes.setdefault(parts[0], parts[1])
    return remotes


def _check(name: str, ok: bool, detail: str) -> dict:
    return {"name": name, "ok": ok, "detail": detail}


def build_report(repo: Path = REPO_ROOT, tag: str | None = None) -> dict:
    checks: list[dict] = []
    remotes = _remotes(repo)
    upstream = remotes.get("upstream")
    checks.append(_check(
        "upstream_remote", bool(upstream) and EXPECTED_UPSTREAM in (upstream or ""),
        f"upstream={upstream or 'missing'}; origin={remotes.get('origin', 'missing')}"))

    py_version, json_version = verify_version_anchor.declared_versions(repo)
    current = None
    if py_version and json_version:
        try:
            current = fork_version.parse(py_version)
            if current != fork_version.parse(json_version):
                checks.append(_check("declarations", False,
                                     f"{py_version} vs {json_version} disagree"))
            else:
                checks.append(_check("declarations", True,
                                     f"{current.display} ({current.pep440})"))
        except ValueError as exc:
            checks.append(_check("declarations", False, str(exc)))
    else:
        checks.append(_check("declarations", False, "pyproject.toml or apps/desktop/package.json "
                                                   "has no version"))

    local_tags = set(_git(repo, "tag", "--list").splitlines())
    candidate = {"tag": tag, "stable": False, "present_locally": False, "base": None,
                 "expected_next": None}
    if not tag:
        checks.append(_check("candidate_tag", False,
                             "no candidate stable tag passed (use --tag vX.Y.Z)"))
    elif not fork_version.is_stable_upstream_tag(tag):
        checks.append(_check("candidate_tag", False,
                             f"{tag!r} is not an upstream stable tag; fork/pre-release tags are rejected"))
    elif tag not in local_tags:
        candidate["stable"] = True
        checks.append(_check("candidate_tag", False,
                             f"{tag!r} is not present locally; fetch is disabled in this preflight"))
    else:
        candidate["stable"] = True
        candidate["present_locally"] = True
        base = tag.lstrip("v")
        candidate["base"] = base
        checks.append(_check("candidate_tag", True, f"{tag} points at {_git(repo, 'rev-parse', tag)[:12]}"))
        if current is not None:
            current_base = fork_version.parse(current.display).base
            if base != current_base:
                nxt = fork_version.ForkVersion(*[int(part) for part in base.split(".")], 1)
            else:
                nxt = fork_version.ForkVersion(*[int(part) for part in base.split(".")], current.fork + 1)
            candidate["expected_next"] = {"display": nxt.display, "pep440": nxt.pep440,
                                          "tag": nxt.tag}

    checklist = repo / CHECKLIST
    text = checklist.read_text(encoding="utf-8-sig") if checklist.exists() else ""
    missing = [anchor for anchor in CHECKLIST_ANCHORS if anchor not in text]
    checks.append(_check("checklist", bool(text) and not missing,
                         "ok" if text and not missing else
                         f"missing {CHECKLIST}" if not text else f"missing anchors: {missing}"))

    drifts = memory_contract.compare()
    checks.append(_check("memory_contract", not drifts,
                         "aligned with evidence/fixtures/memory-contract.json" if not drifts
                         else "; ".join(drifts)))

    dirty = _git(repo, "status", "--porcelain")
    warnings = []
    if dirty:
        warnings.append(f"{len(dirty.splitlines())} uncommitted path(s); commit or stash before rebasing")

    blocked = [f"{check['name']}: {check['detail']}" for check in checks if not check["ok"]]
    return {
        "ok": not blocked,
        "blocked_reason": "; ".join(blocked) if blocked else None,
        "checks": checks,
        "current": {"pyproject": py_version, "desktop": json_version},
        "candidate": candidate,
        "warnings": warnings,
        "network": "not contacted (fetch disabled by this preflight)",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", type=Path, default=REPO_ROOT)
    parser.add_argument("--tag", help="candidate upstream stable tag, e.g. v0.23.0")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args(argv)
    report = build_report(args.repo, args.tag)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"current: {report['current']['pyproject']} / {report['current']['desktop']}")
        for check in report["checks"]:
            print(("PASS " if check["ok"] else "FAIL ") + f"{check['name']}: {check['detail']}")
        for warning in report["warnings"]:
            print(f"WARN {warning}")
        print(f"next versions: {report['candidate']['expected_next']}")
        print("RESULT: " + ("ready for the rebase steps" if report["ok"]
                            else f"blocked -> {report['blocked_reason']}"))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
