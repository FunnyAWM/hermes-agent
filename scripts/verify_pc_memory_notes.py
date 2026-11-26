"""Reproducible PC-side checks for TODO.md T-01, T-02 and T-04.

Usage (from the repo root, in the evidence venv)::

    .venv\\Scripts\\python.exe scripts\\verify_pc_memory_notes.py

Every check runs against a throwaway ``HERMES_HOME`` under the system temp dir, so the real profile
is never touched. Exit code 0 means every check passed; the printed values are what the evidence
files quote, so re-running this script is the whole verification protocol.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

_failures: list[str] = []


def ok(label: str, detail: str = "") -> None:
    print(f"PASS {label}" + (f" :: {detail}" if detail else ""))


def fail(label: str, detail: str = "") -> None:
    _failures.append(label)
    print(f"FAIL {label}" + (f" :: {detail}" if detail else ""))


def check(label: str, condition: bool, detail: str = "") -> bool:
    (ok if condition else fail)(label, detail)
    return condition


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def _memory_limits(store) -> None:
    section("T-01 default limits")
    from hermes_cli.config_defaults import DEFAULT_CONFIG

    memory_cfg = DEFAULT_CONFIG["memory"]
    check("registry default memory_char_limit == 3000",
          memory_cfg["memory_char_limit"] == 3000, str(memory_cfg["memory_char_limit"]))
    check("registry default user_char_limit == 0 (unlimited)",
          memory_cfg["user_char_limit"] == 0, str(memory_cfg["user_char_limit"]))
    check("runtime store memory limit == registry default",
          store.memory_char_limit == memory_cfg["memory_char_limit"], f"{store.memory_char_limit}")
    check("runtime store user limit == registry default",
          store.user_char_limit == memory_cfg["user_char_limit"], f"{store.user_char_limit}")


def _memory_overflow(store, memory_file: Path) -> None:
    section("T-01 over-cap write is refused and the file is untouched")
    first = store.add("memory", "a" * 2900)
    check("add 2,900 chars succeeds", bool(first.get("success")), str(first.get("message"))[:90])
    before = memory_file.read_bytes()
    print(f"usage after accepted write: {store._usage('memory')}")

    overflow = store.add("memory", "b" * 200)
    message = str(overflow.get("message") or overflow.get("error") or "")
    check("over-cap add is refused", not overflow.get("success"), message[:120])
    check("refusal names the shortfall", "free at least" in message, message[:120])
    check("refusal names remaining headroom", "chars remain available" in message, message[:120])
    check("MEMORY.md bytes unchanged after refusal", memory_file.read_bytes() == before,
          f"{len(before)} bytes")
    check("accepted entry not truncated on disk", b"aaaa" in before and len(before) > 2000,
          f"{len(before)} bytes")
    print(f"refusal text: {message}")

    section("T-01 per-target limits are independent")
    user_ok = store.add("user", "prefers terse replies")
    check("first USER.md write creates the file", bool(user_ok.get("success")),
          str(user_ok.get("message"))[:60])
    user_over = store.add("user", "c" * 1400)
    user_msg = str(user_over.get("message") or user_over.get("error") or "")
    check("USER.md accepts past the old 1,375 limit", bool(user_over.get("success")), user_msg[:120])
    check("USER usage renders unlimited", "unlimited" in store._usage("user"), store._usage("user"))


def _data_root(home: Path, memory_file: Path, user_file: Path) -> None:
    section("T-02 data root layout")
    from hermes_constants import get_hermes_home
    from tools.memory_tool import get_memory_dir

    check("get_hermes_home() follows HERMES_HOME", get_hermes_home() == home, str(get_hermes_home()))
    check("get_memory_dir() == <home>/memories", get_memory_dir() == home / "memories",
          str(get_memory_dir()))
    check("memories/MEMORY.md exists", memory_file.is_file(), str(memory_file))
    check("memories/USER.md exists", user_file.is_file(), str(user_file))

    from agent.prompt_builder import build_context_files_prompt, load_soul_md

    default_identity = load_soul_md()
    check("no SOUL.md -> the built-in identity stands in",
          bool(default_identity) and "SOUL_MARKER" not in (default_identity or ""),
          (default_identity or "")[:40])
    (home / "SOUL.md").write_text("SOUL_MARKER: be terse.\n", encoding="utf-8")
    soul = load_soul_md()
    check("SOUL.md at <home>/SOUL.md replaces it", bool(soul and "SOUL_MARKER" in soul),
          (soul or "")[:40])

    project = home / "project"
    project.mkdir()
    (project / "AGENTS.md").write_text("AGENTS_MARKER: run the gates.\n", encoding="utf-8")
    ctx = build_context_files_prompt(cwd=str(project), skip_soul=True)
    check("AGENTS.md from the working directory is loaded", "AGENTS_MARKER" in ctx)

    print("home tree:")
    for path in sorted(home.rglob("*")):
        if path.is_file():
            print(f"  {path.relative_to(home).as_posix()} ({path.stat().st_size} bytes)")


def _notes(home: Path) -> None:
    section("T-04 notes shards: category enum, path safety, index cap")
    from hermes_notes import (
        CATEGORY_DISPLAY_NAMES,
        DEFAULT_HARD_LIMIT,
        DEFAULT_SOFT_LIMIT,
        WRITABLE_CATEGORIES,
        NotesStore,
    )

    expected = {"work", "study", "life", "health", "finance", "people", "other", "docs"}
    check("8 fixed categories including docs", set(CATEGORY_DISPLAY_NAMES) == expected,
          ", ".join(sorted(CATEGORY_DISPLAY_NAMES)))
    check("docs is read-only to the model", set(WRITABLE_CATEGORIES) == expected - {"docs"},
          ", ".join(sorted(WRITABLE_CATEGORIES)))
    check("soft limit defaults to 128 with no hard cap",
          DEFAULT_SOFT_LIMIT == 128 and DEFAULT_HARD_LIMIT is None,
          f"{DEFAULT_SOFT_LIMIT}/{DEFAULT_HARD_LIMIT}")

    store = NotesStore(home)
    rejected = 0
    for bad in ("invalid", "WORK", ""):
        try:
            store.note_path(bad, "topic")
        except ValueError as exc:
            rejected += 1
            assert "work" in str(exc), exc
    for bad_topic in ("../escape", "sub/topic", "..", "."):
        try:
            store.note_path("work", bad_topic)
        except ValueError:
            rejected += 1
    try:
        store.note_path("docs", "imported")
    except ValueError:
        rejected += 1
    check("invalid category, docs write and traversal topics rejected (8/8)", rejected == 8,
          str(rejected))
    ok_path = store.note_path("work", "roadmap")
    check("valid path is notes/work/roadmap.md", ok_path == home / "notes" / "work" / "roadmap.md",
          str(ok_path))
    check(".md suffix is not doubled", store.note_path("work", "roadmap.md") == ok_path)

    shards = {
        ("work", "roadmap"): ("发布路线", "今年两次发布"),
        ("study", "reading"): ("读书笔记", "读了三本书"),
        ("life", "moving"): ("搬家", "十一月搬"),
    }
    for (category, topic), (name, description) in shards.items():
        path = store.note_path(category, topic)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"---\nname: {name}\ndescription: {description}\nupdated: 2026-10-2{len(topic) % 9}\n---\n"
            f"BODY_SECRET_{topic}\n", encoding="utf-8")

    legacy = home / "notes" / "unknown" / "legacy.md"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text("---\nname: 旧笔记\ndescription: 未知类别\n---\nLEGACY_BODY\n", encoding="utf-8")

    imported = home / "notes" / "docs" / "spec.md"
    imported.parent.mkdir(parents=True, exist_ok=True)
    imported.write_text("---\nname: spec\ndescription: 导入的原始文档\n---\nIMPORTED_BODY\n",
                        encoding="utf-8")

    rendered = store.render_index()
    print("rendered index:")
    for line in rendered.splitlines():
        print(f"  {line}")
    check("index header present", rendered.startswith("NOTES INDEX"), rendered[:40])
    check("display name + name + summary shape", "- 工作/发布路线 — 今年两次发布" in rendered)
    check("unknown category bucket reads as 其他", "- 其他/旧笔记 — 未知类别" in rendered)
    check("docs import stays indexed under 文档", "- 文档/spec — 导入的原始文档" in rendered)
    check("bodies are never injected",
          "BODY_SECRET" not in rendered and "LEGACY_BODY" not in rendered
          and "IMPORTED_BODY" not in rendered)
    check("five shards listed", len(store.list_entries()) == 5, str(len(store.list_entries())))

    for index in range(DEFAULT_SOFT_LIMIT + 5):
        path = store.note_path("other", f"cap-{index}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\nname: n{index}\ndescription: d{index}\n---\n", encoding="utf-8")
    check("no hard cap lists every note", len(store.list_entries()) == 5 + DEFAULT_SOFT_LIMIT + 5,
          str(len(store.list_entries())))
    capped = NotesStore(home, soft_limit=DEFAULT_SOFT_LIMIT, hard_limit=DEFAULT_SOFT_LIMIT)
    check("explicit hard cap truncates the index", len(capped.list_entries()) == DEFAULT_SOFT_LIMIT,
          str(len(capped.list_entries())))
    check("hard cap omission is stated", "hard cap" in capped.render_index())


def main() -> int:
    sys.path.insert(0, str(REPO_ROOT))
    with tempfile.TemporaryDirectory(prefix="hermes-pc-evidence-") as tmp:
        home = Path(tmp)
        os.environ["HERMES_HOME"] = str(home)
        print(f"temp HERMES_HOME: {home}")

        from tools.memory_tool_store import MemoryStore

        store = MemoryStore()
        store.load_from_disk()
        memory_file = home / "memories" / "MEMORY.md"
        user_file = home / "memories" / "USER.md"

        _memory_limits(store)
        _memory_overflow(store, memory_file)
        _data_root(home, memory_file, user_file)
        _notes(home)

    print()
    if _failures:
        print(f"RESULT: {len(_failures)} check(s) failed -> {', '.join(_failures)}")
        return 1
    print("RESULT: every check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
