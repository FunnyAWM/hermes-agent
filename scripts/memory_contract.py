"""The cross-client memory contract: one fixture, read by scripts and tests.

The contract lives in ``evidence/fixtures/memory-contract.json`` and mirrors the Android client
(kourichat-next): MEMORY.md model writes default 3,000 chars (0 = unlimited), USER.md uncapped, no
combined budget, code-point + cluster-tail counting, ``\\n§\\n`` entries, seven writable categories
plus read-only ``docs/``, and the root prompt first in the stable tier.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "evidence" / "fixtures" / "memory-contract.json"


def load_fixture(path: Path = FIXTURE) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _store_defaults() -> dict:
    from tools.memory_tool_store import MemoryStore

    signature = inspect.signature(MemoryStore.__init__)
    return {
        "memory_char_limit": signature.parameters["memory_char_limit"].default,
        "user_char_limit": signature.parameters["user_char_limit"].default,
    }


def live_contract() -> dict:
    """Read the contract straight from the running code (signatures, constants, counting probes)."""
    from agent.system_prompt import MEMORY_ROOT_PROMPT
    from hermes_charcount import char_count
    from hermes_notes import CATEGORY_DISPLAY_NAMES, WRITABLE_CATEGORIES
    from tools.memory_tool_store import ENTRY_DELIMITER

    defaults = _store_defaults()
    probes = {
        "plain": "ab",
        "combining": "e\u0301",
        "family_zwj": "\U0001F468\u200d\U0001F469\u200d\U0001F467\u200d\U0001F466",
        "flag": "\U0001F1E8\U0001F1F3",
        "tag_sequence": "\U0001F3F4\U000E0067\U000E0062\U000E0065\U000E006E\U000E0067\U000E007F",
    }
    return {
        "entry_delimiter": ENTRY_DELIMITER,
        **defaults,
        "categories": sorted(CATEGORY_DISPLAY_NAMES),
        "readonly_categories": sorted(set(CATEGORY_DISPLAY_NAMES) - set(WRITABLE_CATEGORIES)),
        "counting": {
            "probes": probes,
            "expected_counts": {name: char_count(value) for name, value in probes.items()},
        },
        "root_prompt": {
            "present": bool(MEMORY_ROOT_PROMPT.strip()),
            "anchor": "running inside Hermes' local memory system",
            "anchor_found": "running inside Hermes' local memory system" in MEMORY_ROOT_PROMPT,
            "text": MEMORY_ROOT_PROMPT,
        },
    }


def compare(live: dict | None = None, fixture: dict | None = None) -> list[str]:
    """Return human-readable drifts between the live code and the shared fixture (empty = aligned)."""
    live = live if live is not None else live_contract()
    fixture = fixture if fixture is not None else load_fixture()
    drifts: list[str] = []
    if live["entry_delimiter"] != fixture["entry_delimiter"]:
        drifts.append(f"delimiter: live={live['entry_delimiter']!r} fixture={fixture['entry_delimiter']!r}")
    for key in ("memory_char_limit", "user_char_limit"):
        if live[key] != fixture[key]:
            drifts.append(f"{key}: live={live[key]} fixture={fixture[key]}")
    expected_categories = sorted(
        set(fixture["categories"]) | set(fixture["readonly_categories"]) | {"other"})
    if live["categories"] != expected_categories:
        drifts.append(f"categories: live={live['categories']} fixture={expected_categories}")
    if live["readonly_categories"] != sorted(fixture["readonly_categories"]):
        drifts.append(f"read-only categories: live={live['readonly_categories']} "
                      f"fixture={sorted(fixture['readonly_categories'])}")
    for name, expected in fixture["counting"]["expected_counts"].items():
        actual = live["counting"]["expected_counts"].get(name)
        if actual != expected:
            drifts.append(f"char_count({name}): live={actual} fixture={expected}")
    if fixture["root_prompt"]["required"] and not live["root_prompt"]["present"]:
        drifts.append("root prompt: missing from agent.system_prompt")
    if fixture["root_prompt"]["required"] and not live["root_prompt"]["anchor_found"]:
        drifts.append("root prompt: fixture anchor text no longer present")
    if fixture.get("combined_limit") is not None:
        drifts.append("fixture asks for a combined limit; PC has none")
    return drifts


def report() -> dict:
    live = live_contract()
    root = live.pop("root_prompt")
    live["root_prompt_present"] = bool(root["present"] and root["anchor_found"])
    drifts = compare()
    return {"ok": not drifts, "fixture": str(FIXTURE), "live": live, "drifts": drifts}
