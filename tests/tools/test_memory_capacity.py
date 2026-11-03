"""Capacity refusals keep disk unchanged across legacy and advertised batch writes."""

import json

import pytest

from tools.memory_tool import MemoryStore, memory_tool


@pytest.mark.parametrize("action", ["add", "patch"])
@pytest.mark.parametrize("batch", [False, True])
def test_refusal_reports_current_headroom_on_both_tool_paths(tmp_path, monkeypatch, action, batch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    store = MemoryStore(memory_char_limit=12)
    store.load_from_disk()
    assert store.add("memory", "fact")["success"]
    path = tmp_path / "memories" / "MEMORY.md"
    before = path.read_bytes()
    operation = {"action": action, "content": "x" * 13}
    if action == "patch":
        operation["old_text"] = "fact"
    args = {"operations": [operation]} if batch else operation
    result = json.loads(memory_tool(target="memory", store=store, **args))

    assert result["success"] is False
    assert "8 chars remain available" in result["error"]
    assert path.read_bytes() == before
    assert store.memory_entries == ["fact"]


def test_multi_operation_refusal_reports_headroom_before_the_batch(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    store = MemoryStore(memory_char_limit=12)
    store.load_from_disk()
    assert store.add("memory", "fact")["success"]
    path = tmp_path / "memories" / "MEMORY.md"
    before = path.read_bytes()
    result = json.loads(memory_tool(target="memory", store=store, operations=[
        {"action": "patch", "old_text": "fact", "content": "other fact"},
        {"action": "add", "content": "second fact"},
    ]))

    assert result["success"] is False
    assert "8 chars remain available" in result["error"]
    assert path.read_bytes() == before
    assert store.memory_entries == ["fact"]
