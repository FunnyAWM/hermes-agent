"""The PC acceptance manifest is the single gate: structural errors must fail."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "verify_pc_acceptance.py"
REAL_MANIFEST = REPO_ROOT / "scripts" / "pc_acceptance_manifest.json"


def _load():
    spec = importlib.util.spec_from_file_location(
        "verify_pc_acceptance_under_test", SCRIPT
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


verify = _load()


def _task(task_id="T-01", **overrides):
    task = {
        "id": task_id,
        "title": "fixture",
        "status": "completed",
        "command": [sys.executable, "-c", "raise SystemExit(0)"],
        "evidence": ["evidence/t01.md"],
        "blocked_reason": None,
        "handoff": None,
    }
    task.update(overrides)
    return task


def _write(tmp_path: Path, tasks) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"schema": 1, "tasks": tasks}), encoding="utf-8")
    return path


def test_real_manifest_is_structurally_valid():
    manifest = verify.load_manifest(REAL_MANIFEST)
    assert verify.validate(manifest) == []
    summary = verify.summarize(manifest)
    blocked = [task for task in manifest["tasks"] if task["status"] == "blocked"]
    assert summary["total"] == len(manifest["tasks"])
    assert summary["blocked"] == len(blocked)
    assert summary["completed"] == summary["total"] - summary["blocked"]
    # Task ids only ever grow with the manifest; a shrink means a task went missing.
    assert len({task["id"] for task in manifest["tasks"]}) == summary["total"]


def test_blocked_task_requires_reason_and_handoff(tmp_path):
    manifest = {"schema": 1, "tasks": [_task(status="blocked", command=None)]}
    errors = verify.validate(manifest)
    assert any("blocked_reason" in error for error in errors)
    assert any("handoff" in error for error in errors)


def test_completed_task_requires_command_and_existing_evidence(tmp_path):
    manifest = {
        "schema": 1,
        "tasks": [_task(command=None, evidence=["evidence/nope.md"])],
    }
    errors = verify.validate(manifest)
    assert any("verification command" in error for error in errors)
    assert any("does not exist" in error for error in errors)


def test_duplicate_and_malformed_ids_are_rejected():
    manifest = {"schema": 1, "tasks": [_task(), _task("T-01"), _task("nope")]}
    errors = verify.validate(manifest)
    assert any("duplicate id" in error for error in errors)
    assert any("must look like" in error for error in errors)


def test_completed_task_must_not_carry_handoff_or_reason():
    manifest = {
        "schema": 1,
        "tasks": [_task(blocked_reason="why", handoff="evidence/t01.md")],
    }
    errors = verify.validate(manifest)
    assert any("must not carry a blocked_reason" in error for error in errors)
    assert any("must not carry a handoff" in error for error in errors)


def test_invalid_manifest_exits_2(tmp_path, capsys):
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    assert verify.main(["--manifest", str(path)]) == 2
    assert "invalid manifest" in capsys.readouterr().err


def test_all_completed_and_passing_commands_exit_0(tmp_path):
    path = _write(tmp_path, [_task()])
    assert verify.main(["--manifest", str(path), "--run"]) == 0


def test_failing_command_exits_1(tmp_path, capsys):
    path = _write(
        tmp_path, [_task(command=[sys.executable, "-c", "raise SystemExit(7)"])]
    )
    assert verify.main(["--manifest", str(path), "--run"]) == 1
    assert "[FAIL] T-01" in capsys.readouterr().out


def test_blocked_manifest_exits_1_unless_allowed(tmp_path):
    path = _write(
        tmp_path,
        [
            _task(),
            _task(
                "T-02",
                status="blocked",
                command=None,
                blocked_reason="needs Android hardware",
                handoff="evidence/handoff/T-09b.md",
            ),
        ],
    )
    assert verify.main(["--manifest", str(path)]) == 1
    assert verify.main(["--manifest", str(path), "--allow-blocked"]) == 3


def test_json_summary_is_pure_json(tmp_path, capsys):
    path = _write(tmp_path, [_task()])
    assert verify.main(["--manifest", str(path), "--run", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"] == {"total": 1, "completed": 1, "blocked": 0}
    assert payload["results"][0]["id"] == "T-01"


def test_out_writes_the_same_report_the_gate_returned(tmp_path):
    path = _write(tmp_path, [_task()])
    report = tmp_path / "nested" / "acceptance.json"
    assert verify.main(["--manifest", str(path), "--run", "--out", str(report)]) == 0
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["exit"] == 0
    assert payload["results"] == [{"id": "T-01", "ok": True, "detail": ""}]


def test_out_records_blocked_exit_3(tmp_path):
    path = _write(
        tmp_path,
        [
            _task(
                "T-02",
                status="blocked",
                command=None,
                blocked_reason="needs Android hardware",
                handoff="evidence/handoff/T-09b.md",
            )
        ],
    )
    report = tmp_path / "acceptance.json"
    assert (
        verify.main(["--manifest", str(path), "--allow-blocked", "--out", str(report)])
        == 3
    )
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["exit"] == 3
    assert payload["blocked"] == ["T-02"]
