"""T-10: deterministic scoring of the two-sided memory answers."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "verify_memory_comparison.py"

GOOD = {
    "Q01": "默认先给结论、默认简短。",
    "Q02": "Windows 11、Android 14，时区 Asia/Hong_Kong。",
    "Q03": "首版只交付 Android 本机模式，多设备同步安排在后续里程碑。",
    "Q04": "UTF-8 Markdown；换模型时保留这些文件。",
    "Q05": "仅供单人使用，不提供团队共享。",
    "Q06": "模型由使用者指定，支持 OpenAI 兼容的 Chat Completions。",
    "Q07": "首版不做向量检索，避免引入 embedding 数据出口。",
    "Q08": "每周三次，每次五公里；右膝疼痛时暂停跑步。",
    "Q09": "每月一百二十元。",
    "Q10": "首版发布日期尚未确定。",
}


def _load():
    spec = importlib.util.spec_from_file_location("memory_comparison_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


comparison = _load()


def _write_answers(tmp_path: Path, answers: dict, **meta) -> Path:
    path = tmp_path / "answers.json"
    path.write_text(json.dumps({"platform": "pc", "model": "fake", **meta, "answers": answers},
                               ensure_ascii=False), encoding="utf-8")
    return path


def test_sample_loads_with_docs_and_unlimited_user():
    questions = comparison.load_questions()
    assert [row["id"] for row in questions] == [f"Q{i:02d}" for i in range(1, 11)]
    assert all(row["forbidden"] for row in questions)


def test_all_answers_correct(tmp_path):
    path = _write_answers(tmp_path, GOOD)
    assert comparison.main(["--answers", str(path)]) == 0


def test_missing_fact_fails(tmp_path):
    answers = dict(GOOD)
    answers["Q08"] = "每周三次。"
    path = _write_answers(tmp_path, answers)
    results = comparison.compare_answers(comparison.load_questions(), answers)
    failed = [row for row in results if not row["ok"]]
    assert [row["id"] for row in failed] == ["Q08"]
    assert "右膝疼痛时暂停跑步" in failed[0]["missing"]
    assert comparison.main(["--answers", str(path)]) == 1


def test_forbidden_phrase_fails(tmp_path):
    answers = dict(GOOD)
    answers["Q06"] = "模型由使用者指定，固定供应商即可。"
    path = _write_answers(tmp_path, answers)
    results = comparison.compare_answers(comparison.load_questions(), answers)
    failed = [row for row in results if not row["ok"]]
    assert [row["id"] for row in failed] == ["Q06"]
    assert "固定供应商" in failed[0]["forbidden_hits"]
    assert comparison.main(["--answers", str(path)]) == 1


def test_incomplete_answers_exit_three(tmp_path):
    answers = dict(GOOD)
    del answers["Q10"]
    path = _write_answers(tmp_path, answers)
    assert comparison.main(["--answers", str(path)]) == 3


def test_flat_answer_file_is_accepted(tmp_path):
    path = tmp_path / "flat.json"
    path.write_text(json.dumps(GOOD, ensure_ascii=False), encoding="utf-8")
    assert comparison.main(["--answers", str(path)]) == 0


def test_json_report_shape(tmp_path, capsys):
    answers = dict(GOOD)
    answers["Q02"] = "Windows 10。"
    path = _write_answers(tmp_path, answers)
    code = comparison.main(["--answers", str(path), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["ok"] is False
    assert payload["failed"][0]["id"] == "Q02"
    assert payload["platform"] == "pc"
    assert payload["missing_answers"] == []
