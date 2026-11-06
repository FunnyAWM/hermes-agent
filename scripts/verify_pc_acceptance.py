#!/usr/bin/env python3
"""Validate (and optionally run) the PC acceptance manifest.

The manifest is the single entry point for "is every PC task actually done?".
Structural validation always runs; ``--run`` additionally executes each
completed task's verification command and reports failures per task.

Exit codes:
  0  all tasks completed (and every command passed with --run)
  1  a completed task's command failed, or blocked tasks exist without --allow-blocked
  2  the manifest itself is invalid
  3  blocked tasks exist and --allow-blocked was passed
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = Path(__file__).with_name("pc_acceptance_manifest.json")
STATUSES = {"completed", "blocked"}
TASK_ID = re.compile(r"^T-\d+[a-z]?$")


def load_manifest(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _require_path(value, *, field: str, task_id: str, errors: list[str]) -> None:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{task_id}: {field} must be a non-empty path")
        return
    if not (REPO_ROOT / value).exists():
        errors.append(f"{task_id}: {field} does not exist: {value}")


def validate(manifest: dict) -> list[str]:
    """Return every structural problem; an empty list means the manifest is usable."""
    errors: list[str] = []
    if manifest.get("schema") != 1:
        errors.append("schema must be 1")
    tasks = manifest.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        return [*errors, "tasks must be a non-empty list"]

    seen: set[str] = set()
    for index, task in enumerate(tasks):
        if not isinstance(task, dict):
            errors.append(f"tasks[{index}] must be an object")
            continue
        task_id = str(task.get("id", f"tasks[{index}]"))
        if not TASK_ID.match(task_id):
            errors.append(f"{task_id}: id must look like T-01 or T-09b")
        if task_id in seen:
            errors.append(f"{task_id}: duplicate id")
        seen.add(task_id)

        if not isinstance(task.get("title"), str) or not task["title"].strip():
            errors.append(f"{task_id}: title is required")

        status = task.get("status")
        if status not in STATUSES:
            errors.append(f"{task_id}: status must be one of {sorted(STATUSES)}")
            continue

        evidence = task.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            errors.append(f"{task_id}: evidence must be a non-empty list")
        else:
            for item in evidence:
                _require_path(item, field="evidence", task_id=task_id, errors=errors)

        command = task.get("command")
        if command is not None and (
            not isinstance(command, list)
            or not command
            or not all(isinstance(part, str) and part for part in command)
        ):
            errors.append(f"{task_id}: command must be a non-empty argv list or null")

        if status == "completed":
            if not command:
                errors.append(f"{task_id}: completed tasks need a verification command")
            if task.get("blocked_reason"):
                errors.append(
                    f"{task_id}: completed task must not carry a blocked_reason"
                )
            if task.get("handoff"):
                errors.append(f"{task_id}: completed task must not carry a handoff")
        else:
            reason = task.get("blocked_reason")
            if not isinstance(reason, str) or not reason.strip():
                errors.append(f"{task_id}: blocked task needs a blocked_reason")
            _require_path(
                task.get("handoff"), field="handoff", task_id=task_id, errors=errors
            )
    return errors


def summarize(manifest: dict) -> dict:
    tasks = manifest.get("tasks", [])
    return {
        "total": len(tasks),
        "completed": sum(task.get("status") == "completed" for task in tasks),
        "blocked": sum(task.get("status") == "blocked" for task in tasks),
    }


def run_tasks(manifest: dict, *, timeout: float) -> list[dict]:
    results: list[dict] = []
    for task in manifest.get("tasks", []):
        if task.get("status") != "completed":
            continue
        command = task.get("command")
        try:
            proc = subprocess.run(
                command,
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=task.get("timeout", timeout),
                check=False,
            )
            ok = proc.returncode == 0
            detail = "" if ok else (proc.stderr or proc.stdout or "").strip()[-400:]
        except (OSError, subprocess.TimeoutExpired) as exc:
            ok = False
            detail = str(exc)
        results.append({"id": task["id"], "ok": ok, "detail": detail})
    return results


def _emit(report: dict, args, text: str) -> None:
    """Print per flag, and always honour --out so a gate run leaves one artifact."""
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--run", action="store_true", help="execute every completed task's command"
    )
    parser.add_argument(
        "--timeout", type=float, default=900.0, help="per-command timeout in seconds"
    )
    parser.add_argument(
        "--allow-blocked",
        action="store_true",
        help="treat blocked tasks as a handoff state (exit 3) instead of a failure",
    )
    parser.add_argument("--json", action="store_true", help="print a JSON summary")
    parser.add_argument("--out", type=Path, help="write the JSON summary to this file")
    args = parser.parse_args(argv)

    try:
        manifest = load_manifest(args.manifest)
    except (OSError, ValueError) as exc:
        print(f"invalid manifest: {exc}", file=sys.stderr)
        _emit(
            {"manifest": str(args.manifest), "errors": [str(exc)], "exit": 2},
            args,
            f"invalid manifest: {exc}",
        )
        return 2

    errors = validate(manifest)
    summary = summarize(manifest)
    report: dict = {
        "manifest": str(args.manifest),
        "summary": summary,
        "errors": errors,
        "results": [],
        "blocked": [],
    }
    if errors:
        report["exit"] = 2
        for error in errors:
            print(f"[FAIL] {error}", file=sys.stderr)
        _emit(report, args, f"{len(errors)} manifest error(s)")
        return 2

    report["blocked"] = [
        task["id"] for task in manifest["tasks"] if task["status"] == "blocked"
    ]

    failures = 0
    if args.run:
        for result in run_tasks(manifest, timeout=args.timeout):
            report["results"].append(result)
            if not result["ok"]:
                failures += 1
            if not args.json and not args.out:
                state = "ok" if result["ok"] else "FAIL"
                print(
                    f"[{state}] {result['id']}"
                    + (f": {result['detail']}" if not result["ok"] else "")
                )

    if failures:
        code = 1
    elif summary["blocked"]:
        code = 3 if args.allow_blocked else 1
    else:
        code = 0
    report["exit"] = code
    _emit(
        report,
        args,
        f"{summary['completed']} completed / {summary['blocked']} blocked / "
        f"{summary['total']} tasks"
        + (f"; {failures} verification command(s) failed" if args.run else ""),
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
