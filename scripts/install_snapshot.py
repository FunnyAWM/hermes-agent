#!/usr/bin/env python3
"""Snapshot file trees as path -> SHA-256 for install-preservation checks (T-12).

Usage::

    python scripts/install_snapshot.py --root <dir> --json before.json
    python scripts/install_snapshot.py --compare before.json after.json --json

Snapshotting records every regular file under each root (path, size, sha256). Missing roots are
recorded as ``{"exists": false}`` so a vanished data directory is a diff, not a silent pass.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def snapshot(roots: list[Path]) -> dict:
    result: dict = {}
    for root in roots:
        key = str(root)
        if not root.exists():
            result[key] = {"exists": False, "files": {}}
            continue
        files = {}
        for path in sorted(root.rglob("*")):
            if path.is_file():
                files[path.relative_to(root).as_posix()] = {
                    "size": path.stat().st_size,
                    "sha256": _file_digest(path),
                }
        result[key] = {"exists": True, "files": files}
    return result


def diff(before: dict, after: dict) -> dict:
    missing, changed, added = [], [], []
    for root, state in before.items():
        current = after.get(root)
        if current is None or not current.get("exists", False):
            missing.append({"root": root, "path": "<root>"})
            continue
        current_files = current.get("files", {})
        for relative, meta in state.get("files", {}).items():
            if relative not in current_files:
                missing.append({"root": root, "path": relative})
            elif current_files[relative] != meta:
                changed.append({"root": root, "path": relative})
        for relative in current_files:
            if relative not in state.get("files", {}):
                added.append({"root": root, "path": relative})
    for root, state in after.items():
        if root not in before:
            added.append({"root": root, "path": "<root>"})
    return {
        "ok": not (missing or changed),
        "missing": missing,
        "changed": changed,
        "added": added,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--root",
        action="append",
        type=Path,
        default=[],
        help="tree to snapshot (repeatable)",
    )
    parser.add_argument("--json", type=Path, help="write snapshot/report here")
    parser.add_argument("--compare", nargs=2, type=Path, metavar=("BEFORE", "AFTER"))
    args = parser.parse_args(argv)

    if args.compare:
        before = json.loads(args.compare[0].read_text(encoding="utf-8-sig"))
        after = json.loads(args.compare[1].read_text(encoding="utf-8-sig"))
        report = diff(before, after)
        if args.json:
            args.json.write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1

    if not args.root:
        parser.error("pass at least one --root, or --compare BEFORE AFTER")
    data = snapshot(args.root)
    if args.json:
        args.json.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(
        json.dumps(
            {root: len(state["files"]) for root, state in data.items()},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
