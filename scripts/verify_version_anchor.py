"""Assert the fork version is one release across pyproject, the desktop package and the git tag.

Usage::

    python scripts/verify_version_anchor.py            # release gate: HEAD must carry the tag
    python scripts/verify_version_anchor.py --commit <rev>
    python scripts/verify_version_anchor.py --json

Exit code 0 means: both declarations parse, describe the same base/fork, and the git tag at the
target revision is exactly ``v<base>-fork.<N>``. ``--allow-distance`` keeps the declaration check
but downgrades "tag not exactly at the revision" to a warning (useful on work-in-progress history).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fork_version

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = "pyproject.toml"
DESKTOP_PACKAGE = "apps/desktop/package.json"


def _git(repo: Path, *args: str) -> str | None:
    result = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=60, check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def show_file(repo: Path, relative: str, rev: str | None) -> str | None:
    if rev is None:
        path = repo / relative
        return path.read_text(encoding="utf-8-sig") if path.exists() else None
    return _git(repo, "show", f"{rev}:{relative}")


def declared_versions(repo: Path, rev: str | None = None) -> tuple[str | None, str | None]:
    pyproject = show_file(repo, PYPROJECT, rev)
    desktop = show_file(repo, DESKTOP_PACKAGE, rev)
    py_version = re.search(r'^version\s*=\s*"([^"]+)"', pyproject or "", re.MULTILINE)
    json_version = re.search(r'"version"\s*:\s*"([^"]+)"', desktop or "")
    return (py_version.group(1) if py_version else None,
            json_version.group(1) if json_version else None)


def check(repo: Path, *, rev: str | None = None, allow_distance: bool = False) -> dict:
    reasons: list[str] = []
    py_version, json_version = declared_versions(repo, rev)
    target = rev or "HEAD"
    report: dict = {
        "ok": False, "target": target, "pyproject": py_version, "desktop": json_version,
        "expected_tag": None, "describe": None, "reasons": reasons,
    }
    if py_version is None:
        reasons.append(f"{PYPROJECT} has no version field")
    if json_version is None:
        reasons.append(f"{DESKTOP_PACKAGE} has no version field")
    if reasons:
        return report
    try:
        parsed = fork_version.parse(py_version)
        desktop = fork_version.parse(json_version)
    except ValueError as exc:
        reasons.append(str(exc))
        return report
    report["expected_tag"] = parsed.tag
    if parsed != desktop:
        reasons.append(f"declarations disagree: {PYPROJECT}={py_version} vs "
                       f"{DESKTOP_PACKAGE}={json_version}")
    if py_version != parsed.pep440:
        reasons.append(f"{PYPROJECT} is not canonical PEP 440: {py_version!r} != {parsed.pep440!r}")
    if json_version != parsed.display:
        reasons.append(f"{DESKTOP_PACKAGE} is not canonical display: {json_version!r} != {parsed.display!r}")

    exact = _git(repo, "describe", "--tags", "--exact-match", target)
    if exact is None:
        described = _git(repo, "describe", "--tags", target)
        report["describe"] = described
        if described is None:
            reasons.append(f"{target} has no reachable tag")
        else:
            nearest = described.rsplit("-", 2)[0] if "-g" in described else described
            if nearest != parsed.tag:
                reasons.append(f"nearest tag {nearest!r} does not match {parsed.tag!r}")
            elif allow_distance:
                report["warning"] = f"{target} is past {parsed.tag}; rebuild with N+1 before release"
            else:
                reasons.append(f"{target} is not exactly at {parsed.tag} ({described}); "
                               "cut the release tag or pass --allow-distance for a work-in-progress check")
    else:
        report["describe"] = exact
        if exact != parsed.tag:
            reasons.append(f"tag at {target} is {exact!r}, expected {parsed.tag!r}")
    report["ok"] = not reasons
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", type=Path, default=REPO_ROOT, help="repository root")
    parser.add_argument("--commit", dest="rev", help="check this revision instead of HEAD")
    parser.add_argument("--allow-distance", action="store_true",
                        help="warn (not fail) when the revision is past the tag")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args(argv)
    report = check(args.repo, rev=args.rev, allow_distance=args.allow_distance)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"target: {report['target']}")
        print(f"pyproject: {report['pyproject']} | desktop: {report['desktop']} | "
              f"tag: {report['expected_tag']} | describe: {report['describe']}")
        if report.get("warning"):
            print(f"WARN {report['warning']}")
        for reason in report["reasons"]:
            print(f"FAIL {reason}")
        print("RESULT: " + ("ok" if report["ok"] else "mismatch"))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
