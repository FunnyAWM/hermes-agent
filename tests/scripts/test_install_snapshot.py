"""T-12: install snapshots and overwrite-preservation diffs."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "install_snapshot.py"


def _load():
    spec = importlib.util.spec_from_file_location("install_snapshot_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


snapshots = _load()


def _seed(root: Path) -> Path:
    (root / "memories").mkdir(parents=True)
    (root / "notes" / "work").mkdir(parents=True)
    (root / "memories" / "MEMORY.md").write_text("T12 memory\n", encoding="utf-8")
    (root / "notes" / "work" / "t12.md").write_text("T12 note\n", encoding="utf-8")
    return root


def test_identical_trees_have_no_diff(tmp_path):
    root = _seed(tmp_path / "home")
    before = snapshots.snapshot([root])
    after = snapshots.snapshot([root])
    report = snapshots.diff(before, after)
    assert report["ok"] and not report["missing"] and not report["changed"]


def test_modified_file_is_changed(tmp_path):
    root = _seed(tmp_path / "home")
    before = snapshots.snapshot([root])
    (root / "memories" / "MEMORY.md").write_text("rewritten\n", encoding="utf-8")
    report = snapshots.diff(before, snapshots.snapshot([root]))
    assert report["ok"] is False
    assert report["changed"] == [{"root": str(root), "path": "memories/MEMORY.md"}]


def test_deleted_file_and_root_are_missing(tmp_path):
    root = _seed(tmp_path / "home")
    before = snapshots.snapshot([root])
    (root / "notes" / "work" / "t12.md").unlink()
    report = snapshots.diff(before, snapshots.snapshot([root]))
    assert report["missing"] == [{"root": str(root), "path": "notes/work/t12.md"}]

    before = snapshots.snapshot([root])
    for child in sorted(root.rglob("*"), reverse=True):
        child.unlink() if child.is_file() else child.rmdir()
    root.rmdir()
    report = snapshots.diff(before, snapshots.snapshot([root]))
    assert report["missing"] == [{"root": str(root), "path": "<root>"}]


def test_added_file_is_reported_but_does_not_fail(tmp_path):
    root = _seed(tmp_path / "home")
    before = snapshots.snapshot([root])
    (root / "memories" / "USER.md").write_text("new\n", encoding="utf-8")
    report = snapshots.diff(before, snapshots.snapshot([root]))
    assert report["ok"] is True
    assert report["added"] == [{"root": str(root), "path": "memories/USER.md"}]


def test_cli_compare_exit_codes(tmp_path, capsys):
    root = _seed(tmp_path / "home")
    before = tmp_path / "before.json"
    after = tmp_path / "after.json"
    assert snapshots.main(["--root", str(root), "--json", str(before)]) == 0
    assert snapshots.main(["--root", str(root), "--json", str(after)]) == 0
    capsys.readouterr()
    assert snapshots.main(["--compare", str(before), str(after)]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True

    (root / "memories" / "MEMORY.md").write_text("changed\n", encoding="utf-8")
    snapshots.main(["--root", str(root), "--json", str(after)])
    assert snapshots.main(["--compare", str(before), str(after)]) == 1
