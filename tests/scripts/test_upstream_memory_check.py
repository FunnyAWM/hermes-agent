"""T-13: the shared memory contract fixture and the live-code comparison."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "memory_contract.py"


def _load():
    spec = importlib.util.spec_from_file_location("memory_contract_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


contract = _load()


def _fixture() -> dict:
    return contract.load_fixture()


def test_live_code_matches_the_shared_fixture():
    assert contract.compare() == []


def test_report_is_serializable_and_clean():
    report = contract.report()
    assert report["ok"] is True
    assert report["drifts"] == []
    assert report["live"]["memory_char_limit"] == 3000
    assert report["live"]["user_char_limit"] == 0
    assert report["live"]["root_prompt_present"] is True
    json.dumps(report)


def test_limit_drift_is_reported():
    fixture = _fixture()
    fixture["memory_char_limit"] = 2200
    fixture["user_char_limit"] = 1375
    drifts = contract.compare(fixture=fixture)
    assert any(drift.startswith("memory_char_limit") for drift in drifts)
    assert any(drift.startswith("user_char_limit") for drift in drifts)


def test_delimiter_and_counting_drift_are_reported():
    fixture = _fixture()
    fixture["entry_delimiter"] = "§"
    drifts = contract.compare(fixture=fixture)
    assert any(drift.startswith("delimiter") for drift in drifts)

    fixture = _fixture()
    fixture["counting"]["expected_counts"]["flag"] = 2
    drifts = contract.compare(fixture=fixture)
    assert any("char_count(flag)" in drift for drift in drifts)


def test_category_and_docs_drift_are_reported():
    fixture = _fixture()
    fixture["readonly_categories"] = []
    drifts = contract.compare(fixture=fixture)
    assert any("read-only categories" in drift for drift in drifts)

    fixture = _fixture()
    fixture["categories"] = ["work", "other"]
    drifts = contract.compare(fixture=fixture)
    assert any(drift.startswith("categories") for drift in drifts)


def test_root_prompt_drift_is_reported(monkeypatch):
    live = contract.live_contract()
    live["root_prompt"]["present"] = False
    drifts = contract.compare(live=live)
    assert any("root prompt: missing" in drift for drift in drifts)

    live = contract.live_contract()
    live["root_prompt"]["anchor_found"] = False
    drifts = contract.compare(live=live)
    assert any("anchor text" in drift for drift in drifts)


def test_combined_limit_in_fixture_is_a_drift():
    fixture = _fixture()
    fixture["combined_limit"] = 3575
    assert any("combined limit" in drift for drift in contract.compare(fixture=fixture))
