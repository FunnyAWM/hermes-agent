"""Verify the shared T-10 sample and compare two-sided answers; no model calls.

Default mode checks that the sample loads through the real memory/notes readers.
``--answers <json>`` additionally scores collected answers deterministically:
every ``expected`` fact must appear, no ``forbidden`` phrase may appear.

Usage::

    python scripts/verify_memory_comparison.py
    python scripts/verify_memory_comparison.py --answers answers/pc.json
    python scripts/verify_memory_comparison.py --answers answers/pc.json --json

Exit codes: 0 all good, 1 a fact is missing or a forbidden phrase appears,
3 answers are missing for one or more questions (collection incomplete).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "evidence" / "fixtures" / "memory-comparison"


def load_questions() -> list[dict]:
    questions = json.loads((SAMPLE / "questions.json").read_text(encoding="utf-8-sig"))
    ids = [row["id"] for row in questions]
    assert len(questions) == 10 and len(set(ids)) == 10, ids
    for row in questions:
        source = "\n".join(
            (SAMPLE / "home" / path).read_text(encoding="utf-8-sig") for path in row["sources"])
        assert row["question"] and row["reject"]
        assert row["expected"] and all(fact in source for fact in row["expected"]), row["id"]
        assert isinstance(row.get("forbidden", []), list)
    return questions


def compare_answers(questions: list[dict], answers: dict[str, str]) -> list[dict]:
    results = []
    for row in questions:
        answer = answers.get(row["id"])
        missing = [fact for fact in row["expected"] if fact not in (answer or "")]
        hit = [phrase for phrase in row.get("forbidden", []) if phrase in (answer or "")]
        results.append({
            "id": row["id"], "ok": not missing and not hit,
            "missing": missing, "forbidden_hits": hit,
        })
    return results


def _answers_from(payload: dict) -> tuple[dict[str, str], dict]:
    if isinstance(payload.get("answers"), dict):
        meta = {key: value for key, value in payload.items() if key != "answers"}
        answers = payload["answers"]
    else:
        meta = {key: payload[key] for key in ("platform", "model", "generated_at", "notes")
                if key in payload}
        answers = {key: value for key, value in payload.items() if key not in meta}
    return {str(key): str(value) for key, value in answers.items()}, meta


def _check_sample() -> None:
    with tempfile.TemporaryDirectory(prefix="hermes-t10-") as tmp:
        home = Path(tmp) / "home"
        shutil.copytree(SAMPLE / "home", home)
        os.environ["HERMES_HOME"] = str(home)
        from agent.system_prompt import MEMORY_ROOT_PROMPT, _memory_parts
        from hermes_notes import NotesStore, WRITABLE_CATEGORIES
        from tools.memory_tool_store import ENTRY_DELIMITER, MemoryStore

        memory = MemoryStore()
        memory.load_from_disk()
        for target, entries, limit in (("memory", memory.memory_entries, memory.memory_char_limit),
                                       ("user", memory.user_entries, memory.user_char_limit)):
            count = len(ENTRY_DELIMITER.join(entries))
            if limit > 0:
                assert count <= limit, f"{target}: {count} > {limit}"
            print(f"PASS {target}: {count} chars / {'unlimited' if limit <= 0 else limit}, "
                  f"{len(entries)} entries")
        assert memory.memory_char_limit == 3000 and memory.user_char_limit == 0

        notes = NotesStore(home)
        entries = notes.list_entries()
        assert len(entries) == 4, len(entries)
        assert any(entry.category == "docs" for entry in entries), "docs sample missing"
        assert "docs" not in WRITABLE_CATEGORIES
        try:
            notes.note_path("docs", "written-by-model")
        except ValueError:
            pass
        else:
            raise AssertionError("notes/docs must reject model writes")
        parts = _memory_parts(SimpleNamespace(
            _memory_store=memory, _memory_enabled=True, _user_profile_enabled=True, _memory_manager=None,
        ))
        assert parts[:2] == [memory.format_for_system_prompt("memory"), memory.format_for_system_prompt("user")]
        assert parts[2] == notes.render_index()
        for entry in entries:
            assert entry.display_line in parts[2]
        assert "首版发布日期尚未确定" not in parts[2]
        assert "DOC_BODY_MARKER" not in parts[2]
        assert "running inside Kouri Harness' local memory system" in MEMORY_ROOT_PROMPT
        print("PASS new-session prompt: MEMORY -> USER -> notes summaries (docs indexed, bodies read-only)")


def _print_hashes() -> None:
    for path in sorted(SAMPLE.rglob("*")):
        if path.is_file() and path.name != "README.md":
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            print(f"SHA256 {digest} {path.relative_to(SAMPLE).as_posix()}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--answers", type=Path, help="collected answers JSON")
    parser.add_argument("--json", action="store_true", help="emit the comparison as JSON")
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    questions = load_questions()
    if args.json:
        import contextlib
        import io

        with contextlib.redirect_stdout(io.StringIO()):
            _check_sample()
    else:
        _check_sample()
        _print_hashes()
        print("PASS 10 questions: every expected fact is present in its declared sources")
    if not args.answers:
        if args.json:
            print(json.dumps({"ok": True, "answers": None, "questions": len(questions)},
                             ensure_ascii=False, indent=2))
        else:
            print("Model answers and Android comparison remain unverified.")
        return 0

    payload = json.loads(args.answers.read_text(encoding="utf-8-sig"))
    answers, meta = _answers_from(payload)
    results = compare_answers(questions, answers)
    missing_ids = [row["id"] for row in questions if not str(answers.get(row["id"], "")).strip()]
    failed = [result for result in results if not result["ok"]]
    report = {"ok": not failed and not missing_ids, "missing_answers": missing_ids,
              "failed": failed, "platform": meta.get("platform"), "model": meta.get("model")}
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for result in results:
            if result["ok"]:
                print(f"PASS {result['id']}")
            elif result["id"] in missing_ids:
                print(f"MISSING {result['id']}")
            else:
                print(f"FAIL {result['id']} missing={result['missing']} "
                      f"forbidden={result['forbidden_hits']}")
        print(f"answers: {meta.get('platform') or 'unknown platform'} / {meta.get('model') or 'unknown model'}")
    if missing_ids:
        return 3
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
