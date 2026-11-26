"""Reproducible PC-side check for TODO.md T-05 (PRD 记 06, data half).

The live half of T-05 is a model conversation that runs to the context edge, summarises, extracts
facts into ``notes/`` and compacts the same persistent session. That needs model credentials and a real session, so
it stays blocked and is tracked separately. What this script *can* pin down is the part that would
silently corrupt the hand-off: after a near-cap consolidation, does the memory file stay within
its 3000-char model-write limit, does the ``\\n§\\n`` counting rule survive, does the extracted note use PRD §7.3 front
matter, and does a reloaded store render MEMORY → USER → notes index without the
dropped entries?

Usage (repo root, evidence venv)::

    .venv\\Scripts\\python.exe scripts\\verify_consolidation_chain.py

Exit code 0 means every check passed; the printed values are what ``evidence/t05.md`` quotes.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[1]

_failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> bool:
    print(f"{'PASS' if condition else 'FAIL'} {label}" + (f" :: {detail}" if detail else ""))
    if not condition:
        _failures.append(label)
    return condition


def _fill(store, memory_limit: int) -> list[str]:
    facts = [
        "工作：Hermes 桌面端以 0.22.5-fork.1 交付，跟随上游稳定 tag 升级（T-07）。",
        "环境：Windows 11 + Python 3.14.2 + Node 24；桌面构建用仓库自带 electron-builder。",
        "约定：记忆写入超限必须拒绝并返回剩余字符数，不静默截断（PRD §7.2）。",
        "约定：笔记目录名只能用 work/study/life/health/finance/people/other。",
        "偏好：回答先给结论，再给依据；不写客套话。",
        "排查：Windows 上 pytest 的 chmod 断言只适用于 posix，已按平台标记。",
        "流程：任务收尾先改 TODO.md，再写 MEMORY.md，最后回报。",
    ]
    for fact in facts:
        result = store.add("memory", fact)
        assert result.get("success"), result
    from tools.memory_tool_store import ENTRY_DELIMITER

    stale = "旧结论：桌面端还在用 MSIX 打包（已作废，改 nsis + portable）。"
    padding = memory_limit - store._char_count("memory") - len(ENTRY_DELIMITER) - len(stale) - 32
    stale += "旧打包说明。" * (padding // len("旧打包说明。"))
    result = store.add("memory", stale)
    assert result.get("success"), result
    facts.append(stale)
    assert 0 <= memory_limit - store._char_count("memory") <= 40
    return facts


def main() -> int:
    sys.path.insert(0, str(REPO_ROOT))
    with tempfile.TemporaryDirectory(prefix="hermes-t05-") as tmp:
        home = Path(tmp)
        os.environ["HERMES_HOME"] = str(home)
        print(f"temp HERMES_HOME: {home}")

        from tools.memory_tool_store import ENTRY_DELIMITER, MemoryStore

        store = MemoryStore()
        store.load_from_disk()
        facts = _fill(store, store.memory_char_limit)
        store.add("user", "称呼：直接叫名字；时区 Asia/Hong_Kong。")
        before_overflow = (home / "memories" / "MEMORY.md").read_bytes()

        # 1. Near the cap, a new fact is refused with the remaining headroom, not truncated.
        overflow = store.add("memory", "新事实：" + "压" * 260)
        message = str(overflow.get("message") or overflow.get("error") or "")
        check("near-cap add is refused", not overflow.get("success"), message[:90])
        check("refusal reports chars still available", "chars remain available" in message)
        check("stored entries are untouched after refusal", len(store.memory_entries) == len(facts))
        check("file is byte-identical after refusal",
              (home / "memories" / "MEMORY.md").read_bytes() == before_overflow)

        # 2. Consolidation: fold two verbose entries into one conclusion and drop the stale MSIX one.
        replaced = store.replace(
            "memory",
            "工作：Hermes 桌面端以 0.22.5-fork.1 交付",
            "工作：桌面端 0.22.5-fork.1，Windows 出 nsis + portable，跟随上游稳定 tag 升级。",
        )
        check("verbose entry collapsed via replace", bool(replaced.get("success")), str(replaced.get("message"))[:60])
        removed = store.remove("memory", "旧结论：桌面端还在用 MSIX 打包")
        check("stale entry dropped via remove", bool(removed.get("success")), str(removed.get("message"))[:60])
        added = store.add("memory", "打包：Windows 走 NSIS（per-user）+ portable，不做自动更新。")
        check("new fact fits after consolidation", bool(added.get("success")), str(added.get("message"))[:60])

        # 3. The fact that did not fit is extracted into a note using PRD §7.3 front matter.
        from hermes_notes import NotesStore

        notes = NotesStore(home)
        note_path = notes.note_path("work", "hermes-packaging")
        note_path.parent.mkdir(parents=True, exist_ok=True)
        note_path.write_text(
            "---\n"
            "name: Hermes 打包方案\n"
            "description: Windows 走 NSIS 与 portable，不做自动更新，升级靠覆盖安装\n"
            "updated: 2026-11-03\n"
            "---\n\n"
            "Windows 用 NSIS per-user 安装加 portable 试跑；覆盖安装只换程序目录，\n"
            "HERMES_HOME 下的 memories/ 与 notes/ 不动。\n",
            encoding="utf-8",
        )
        front = note_path.read_text(encoding="utf-8-sig").split("\n---\n", 1)[0]
        fields = dict(
            line.split(": ", 1) for line in front.splitlines() if ": " in line and not line.startswith("---")
        )
        check("note carries name/description/updated", set(fields) == {"name", "description", "updated"},
              ", ".join(sorted(fields)))
        check("description stays on one line and <= 40 chars", len(fields["description"]) <= 40,
              str(len(fields["description"])))
        check("topic lives under notes/work/", note_path == home / "notes" / "work" / "hermes-packaging.md")

        # 4. 数据重载校验；同会话 ID 与历史归档另由集成测试覆盖。
        fresh = MemoryStore()
        fresh.load_from_disk()
        memory_block = fresh.format_for_system_prompt("memory") or ""
        user_block = fresh.format_for_system_prompt("user") or ""
        index = notes.render_index()

        check("fresh store still within MEMORY limit", fresh._char_count("memory") <= 3000,
              f"{fresh._char_count('memory')}/3000")
        check("USER.md is uncapped", fresh.user_char_limit == 0, f"{fresh.user_char_limit}")
        check("no combined memory budget", not hasattr(fresh, "combined_char_limit"))
        check("count is delimited entries, not the banner",
              fresh._char_count("memory") == len(ENTRY_DELIMITER.join(fresh.memory_entries)),
              f"delimiter={ENTRY_DELIMITER!r}")
        check("banner percentage matches the counted chars",
              f"{fresh._char_count('memory'):,}/3,000 chars" in memory_block, memory_block.splitlines()[1] if memory_block else "")
        check("consolidated conclusion survives store reload", "nsis + portable" in memory_block)
        check("dropped MSIX conclusion is gone", "MSIX" not in memory_block)
        check("notes index shows display name/name/summary",
              "- 工作/Hermes 打包方案 — Windows 走 NSIS 与 portable" in index, index.splitlines()[-1])
        check("note body is not injected", "覆盖安装只换程序目录" not in index)

        # 5. Fresh-session prompt order: MEMORY -> USER -> notes index, the PRD §7.5 tail.
        from agent.system_prompt import _memory_parts

        stub = SimpleNamespace(
            _memory_store=fresh,
            _memory_enabled=True,
            _user_profile_enabled=True,
            _memory_manager=None,
        )
        parts = _memory_parts(stub)
        check("three tail parts rendered (MEMORY, USER, notes)", len(parts) == 3, str(len(parts)))
        check("tail order is MEMORY -> USER -> notes",
              bool(parts) and parts[0] == memory_block and parts[1] == user_block
              and parts[2].startswith("NOTES INDEX"))

        print("\nrendered tail (first line of each part):")
        for part in parts:
            print(f"  {part.splitlines()[0]}")

    print()
    if _failures:
        print(f"RESULT: {len(_failures)} check(s) failed -> {', '.join(_failures)}")
        return 1
    print("RESULT: every check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
