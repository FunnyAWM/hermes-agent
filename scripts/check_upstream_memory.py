#!/usr/bin/env python3
"""Check the cross-client memory contract before accepting an upstream release.

Read-only review aid, not a migration: it compares the running code with the shared fixture
(``evidence/fixtures/memory-contract.json``) and, with ``--tag``, shows the candidate release's
declared version. It never fetches tags, edits files, or declares an upgrade passed.
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

import memory_contract

DELIMITER = memory_contract.load_fixture()["entry_delimiter"]


def _count_entries(path: Path) -> int:
    if not path.exists():
        return 0
    text = path.read_text(encoding="utf-8-sig")
    return len([entry for entry in text.split(DELIMITER) if entry.strip()])


def _tracked_version(tag: str) -> str:
    result = subprocess.run(
        ["git", "show", f"{tag}:pyproject.toml"], cwd=REPO_ROOT,
        text=True, encoding="utf-8", capture_output=True, check=False, timeout=30,
    )
    if result.returncode:
        return "unavailable"
    for line in result.stdout.splitlines():
        if line.strip().startswith("version"):
            return line.split("=", 1)[1].strip().strip('"\'')
    return "unknown"


def build_report(tag: str | None) -> dict:
    report = memory_contract.report()
    memories = REPO_ROOT / "memories"
    report["local_counts"] = {
        "MEMORY.md": _count_entries(memories / "MEMORY.md"),
        "USER.md": _count_entries(memories / "USER.md"),
    }
    if tag:
        report["candidate"] = {"tag": tag, "pyproject_version": _tracked_version(tag)}
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tag", help="candidate tag to read the declared version from (no fetch)")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args(argv)
    report = build_report(args.tag)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1

    live = report["live"]
    print(f"fixture: {report['fixture']}")
    print(f"delimiter: {live['entry_delimiter']!r}")
    print(f"limits: MEMORY.md={live['memory_char_limit']} (0=unlimited), "
          f"USER.md={live['user_char_limit']} (0=unlimited), combined=none")
    print("categories: " + ", ".join(live["categories"]))
    print("read-only: " + ", ".join(live["readonly_categories"]))
    print("char_count probes: " + ", ".join(
        f"{name}={count}" for name, count in live["counting"]["expected_counts"].items()))
    print("root prompt present: " + str(live["root_prompt_present"]))
    print(f"local entries: MEMORY.md={report['local_counts']['MEMORY.md']}, "
          f"USER.md={report['local_counts']['USER.md']}")
    if "candidate" in report:
        print(f"candidate tag: {report['candidate']['tag']} "
              f"(pyproject version: {report['candidate']['pyproject_version']})")
    for drift in report["drifts"]:
        print(f"DRIFT {drift}")
    print("Review the candidate diff for: Markdown/UTF-8 preservation; 3,000-char MEMORY.md cap "
          "(0 = unlimited) and uncapped USER.md; cluster-tail counting; full-delimiter entries; "
          "read-only notes/docs; root prompt first in the stable tier; reject-without-write.")
    print("RESULT: " + ("contract aligned" if report["ok"] else "drift detected"))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
