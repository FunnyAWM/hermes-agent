"""Capture the real system-prompt section order for TODO.md T-03 / D4.

Builds a minimal agent stub over a throwaway `HERMES_HOME`, calls the real
`agent.system_prompt.build_system_prompt_parts`, and prints where each section
actually lands. This is the reproducible protocol TODO.md §4.5 asks for.

Usage::

    .venv\\Scripts\\python.exe scripts\\capture_prompt_order.py

Exit code 0 means all four T-03 / T-18 criteria hold:

0. the shared memory root prompt comes before SOUL and everything else;
1. no skills-index line appears before the MEMORY block;
2. MEMORY -> USER -> notes index are three consecutive volatile sections;
3. the SOUL section is the real SOUL.md, not the built-in default identity.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[1]

ROOT_MARKER = "running inside Kouri Harness' local memory system"
SOUL_MARKER = "SOUL_MARKER"
MEMORY_MARKER = "MEMORY (your personal notes)"
USER_MARKER = "USER PROFILE"
NOTES_MARKER = "NOTES INDEX"
SKILLS_MARKER = "- stub-skill:"

_failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"PASS {label}" + (f" :: {detail}" if detail else ""))
        return
    _failures.append(label)
    print(f"FAIL {label}" + (f" :: {detail}" if detail else ""))


def _stub_agent(memory_store):
    return SimpleNamespace(
        load_soul_identity=True,
        skip_context_files=False,
        valid_tool_names=["skills_list"],
        _task_completion_guidance=False,
        _tool_use_enforcement=False,
        _environment_probe=False,
        _kanban_worker_guidance="",
        _memory_store=memory_store,
        _memory_manager=None,
        _memory_enabled=True,
        _user_profile_enabled=True,
        model="stub-model",
        provider="stub-provider",
        platform="cli",
        pass_session_id=False,
        session_id="t03-capture",
        _emit_status=lambda *_a, **_k: None,
    )


def main() -> int:
    sys.path.insert(0, str(REPO_ROOT))
    with tempfile.TemporaryDirectory(prefix="hermes-t03-", ignore_cleanup_errors=True) as tmp:
        home = Path(tmp) / "home"
        project = Path(tmp) / "project"
        (home / "memories").mkdir(parents=True)
        project.mkdir()
        os.environ["HERMES_HOME"] = str(home)

        (home / "SOUL.md").write_text("SOUL_MARKER: stay terse, never pad.\n", encoding="utf-8")
        (project / "AGENTS.md").write_text("AGENTS_MARKER: run the gates before you report.\n",
                                           encoding="utf-8")
        (home / "memories" / "MEMORY.md").write_text(
            "Deploys: Fly.io, manual approval.\n§\nHost: forge", encoding="utf-8")
        (home / "memories" / "USER.md").write_text("Prefers terse answers.", encoding="utf-8")
        note = home / "notes" / "work" / "roadmap.md"
        note.parent.mkdir(parents=True)
        note.write_text("---\nname: 路线图\ndescription: 今年两次发布\nupdated: 2026-10-20\n---\nBODY\n",
                        encoding="utf-8")

        os.chdir(project)
        import agent.system_prompt as sp
        from tools.memory_tool_store import MemoryStore

        # The real index needs the whole skills subsystem; a marker keeps the
        # position observable without dragging in that dependency.
        sp._skills_prompt = lambda agent: SKILLS_MARKER + " stub skill index"

        store = MemoryStore()
        store.load_from_disk()
        agent = _stub_agent(store)
        parts = sp.build_system_prompt_parts(agent)
        prompt = "\n\n".join(parts[key] for key in ("stable", "context", "volatile"))

        print("tiers: " + ", ".join(f"{k}={len(v):,} chars" for k, v in parts.items()))
        markers = {
            "ROOT": ROOT_MARKER,
            "SOUL": SOUL_MARKER,
            "AGENTS": "AGENTS_MARKER",
            "MEMORY": MEMORY_MARKER,
            "USER": USER_MARKER,
            "NOTES": NOTES_MARKER,
            "SKILLS": SKILLS_MARKER,
        }
        offsets = {name: prompt.find(marker) for name, marker in markers.items()}
        print("\nsection offsets in the assembled prompt:")
        for name, offset in offsets.items():
            print(f"  {name:<7} {offset:>7}  {markers[name]}")
        print("\nvolatile sections in order:")
        for line in parts["volatile"].split("\n\n"):
            print(f"  | {line.splitlines()[0][:90]}")

        missing = [name for name, offset in offsets.items() if offset < 0]
        check("every section is present", not missing, ", ".join(missing) or "all seven")
        check("ROOT comes first", 0 <= offsets["ROOT"] < offsets["SOUL"],
              f"root at {offsets['ROOT']}, soul at {offsets['SOUL']}")
        check("SOUL comes before AGENTS", 0 <= offsets["SOUL"] < offsets["AGENTS"])
        check("AGENTS comes before MEMORY", offsets["AGENTS"] < offsets["MEMORY"])
        check("no skills-index line before MEMORY", offsets["SKILLS"] > offsets["MEMORY"],
              f"skills at {offsets['SKILLS']}, memory at {offsets['MEMORY']}")
        check("MEMORY -> USER -> NOTES are consecutive",
              0 <= offsets["MEMORY"] < offsets["USER"] < offsets["NOTES"] < offsets["SKILLS"])
        gap_between = prompt[offsets["MEMORY"]:offsets["NOTES"]]
        check("nothing is injected between MEMORY and NOTES",
              SKILLS_MARKER not in gap_between,
              f"{len(gap_between)} chars between them")
        check("SOUL.md really loaded (not the default identity)",
              "SOUL_MARKER" in parts["stable"] and "You are Hermes Agent, built by Nous Research"
              not in parts["stable"].split("SOUL_MARKER")[0])
        print("\n--- volatile tier verbatim ---")
        print(parts["volatile"])

    print()
    if _failures:
        print(f"RESULT: {len(_failures)} criterion(s) failed -> {', '.join(_failures)}")
        return 1
    print("RESULT: all T-03 / T-18 criteria hold")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
