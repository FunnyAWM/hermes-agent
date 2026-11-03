#!/usr/bin/env python3
"""Check the cross-client memory contract before accepting an upstream release.

This is a read-only review aid, not a migration. It reports the current parser,
limits and entry-counting rules that must be compared with the upstream diff.
"""
from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MEMORY_STORE = ROOT / "tools" / "memory_tool_store.py"
PROJECT = ROOT / "pyproject.toml"
MEMORY = ROOT / "memories" / "MEMORY.md"
USER = ROOT / "memories" / "USER.md"
DELIMITER = "\n§\n"


def _project_version() -> str:
    match = re.search(r'^version\s*=\s*["\']([^"\']+)', PROJECT.read_text(encoding="utf-8-sig"), re.MULTILINE)
    return match.group(1) if match else "unknown"


def _limits() -> tuple[int, int]:
    source = MEMORY_STORE.read_text(encoding="utf-8-sig")
    match = re.search(r"def __init__\(self, memory_char_limit: int = (\d+), user_char_limit: int = (\d+)", source)
    if not match:
        raise RuntimeError("Could not locate memory defaults in tools/memory_tool_store.py")
    return int(match.group(1)), int(match.group(2))


def _count(path: Path) -> int:
    if not path.exists():
        return 0
    text = path.read_text(encoding="utf-8-sig")
    entries = [entry.strip() for entry in text.split(DELIMITER) if entry.strip()]
    return len(DELIMITER.join(entries))


def _tracked_version(tag: str) -> str:
    result = subprocess.run(
        ["git", "show", f"{tag}:pyproject.toml"], cwd=ROOT,
        text=True, encoding="utf-8", capture_output=True, check=False, timeout=30,
    )
    if result.returncode:
        return "unavailable"
    match = re.search(r'^version\s*=\s*["\']([^"\']+)', result.stdout, re.MULTILINE)
    return match.group(1) if match else "unknown"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", help="upstream stable tag admitted for this review")
    args = parser.parse_args()
    memory_limit, user_limit = _limits()
    print(f"current pyproject version: {_project_version()}")
    print(f"current memory limits: MEMORY.md={memory_limit}, USER.md={user_limit} characters")
    print(f"current entry delimiter: {DELIMITER.encode('unicode_escape').decode('ascii')}")
    print(f"current count: MEMORY.md={_count(MEMORY)}, USER.md={_count(USER)} characters")
    if args.tag:
        print(f"candidate tag pyproject version: {_tracked_version(args.tag)}")
    print("Review the candidate diff for: Markdown/UTF-8 preservation; limits (2200/1375 by PRD);")
    print("character counting excluding render banners; full-delimiter entry splitting; reject-without-write behavior.")
    print("This script does not fetch tags, edit files, or declare the review passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
