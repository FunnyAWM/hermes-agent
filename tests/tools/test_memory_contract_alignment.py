"""Shared memory contract with the Android client (TODO T-17).

Android treats only model writes to MEMORY.md as capped (default 3000, 0 =
unlimited), leaves USER.md uncapped, and counts code points plus their cluster
tail so an emoji sequence is one character. These tests pin that contract.
"""

import json

import pytest

from hermes_charcount import char_count
from tools.memory_tool import MemoryStore, memory_tool


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", 0),
        ("abc", 3),
        ("中文", 2),
        ("e\u0301", 1),          # combining acute
        ("👍🏽", 1),              # emoji + skin-tone modifier
        ("👨\u200d👩\u200d👧", 1),  # ZWJ family
        ("🇨🇳", 1),              # regional-indicator pair
        ("🏴\U000E0067\U000E0062\U000E007F", 1),  # tag sequence
        ("a\u0301b", 2),        # cluster tail does not swallow the next base
    ],
)
def test_char_count_matches_android_cluster_rule(text, expected):
    assert char_count(text) == expected


def test_defaults_are_3000_and_unlimited_user():
    store = MemoryStore()
    assert store.memory_char_limit == 3000
    assert store.user_char_limit == 0


def test_user_profile_accepts_more_than_the_old_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    store = MemoryStore()
    store.load_from_disk()

    result = store.add("user", "u" * 2000)

    assert result["success"] is True
    assert "unlimited" in result["usage"]
    assert (tmp_path / "memories" / "USER.md").read_text(encoding="utf-8") == "u" * 2000


def test_memory_over_the_default_cap_is_refused_without_touching_disk(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    store = MemoryStore()
    store.load_from_disk()
    path = tmp_path / "memories" / "MEMORY.md"
    path.write_text("fact", encoding="utf-8")
    store.load_from_disk()
    before = path.read_bytes()

    result = json.loads(memory_tool(
        target="memory", store=store, operations=[{"action": "add", "content": "x" * 3100}],
    ))

    assert result["success"] is False
    assert "3,000" in result["error"]
    assert "3,100" in result["error"]
    assert path.read_bytes() == before


def test_zero_memory_limit_means_unlimited(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    store = MemoryStore(memory_char_limit=0)
    store.load_from_disk()

    assert store.add("memory", "m" * 5000)["success"] is True
    assert "unlimited" in store._usage("memory")


def test_unlimited_limit_does_not_report_a_noncompliance_on_load(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    path = tmp_path / "memories" / "USER.md"
    path.parent.mkdir(parents=True)
    path.write_text("u" * 5000, encoding="utf-8")

    store = MemoryStore()
    store.load_from_disk()

    assert store.user_entries == ["u" * 5000]
    assert "unlimited" in store._usage("user")
