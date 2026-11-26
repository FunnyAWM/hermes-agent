"""The memory write trigger is restated at the tail of the prompt (PRD §7.5).

Android T-88 measured the failure this guards: the model explained that a fact
belonged in notes/ and then never wrote it. The root conventions say the same
thing at the top of a long, tool-heavy prompt; this reminder keeps the trigger
as the last instruction the model reads, gated on a surface that can persist.
"""

from types import SimpleNamespace
from unittest.mock import patch

from agent.system_prompt import MEMORY_WRITE_REMINDER, build_system_prompt, build_system_prompt_parts


def _agent(**overrides):
    base = dict(
        load_soul_identity=False,
        skip_context_files=True,
        valid_tool_names=["write_file"],
        _task_completion_guidance=False,
        _tool_use_enforcement=False,
        _environment_probe=False,
        _kanban_worker_guidance="",
        _memory_store=None,
        _memory_manager=None,
        model="",
        provider="",
        platform="",
        pass_session_id=False,
        session_id="",
        _emit_status=lambda *_args, **_kwargs: None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _parts(agent, monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    with patch("agent.system_prompt._skills_prompt", return_value="SKILLS_MARKER"):
        return build_system_prompt_parts(agent)


def test_reminder_is_the_last_block_before_the_conversation(tmp_path, monkeypatch):
    parts = _parts(_agent(), monkeypatch, tmp_path)

    assert parts["volatile"].endswith(MEMORY_WRITE_REMINDER)
    assert parts["stable"].count("Memory write trigger") == 0


def test_reminder_with_only_the_memory_tool(tmp_path, monkeypatch):
    parts = _parts(_agent(valid_tool_names=["memory"]), monkeypatch, tmp_path)

    assert parts["volatile"].endswith(MEMORY_WRITE_REMINDER)


def test_reminder_is_absent_without_a_writable_surface(tmp_path, monkeypatch):
    parts = _parts(_agent(valid_tool_names=["read_file", "web_search"]), monkeypatch, tmp_path)

    assert MEMORY_WRITE_REMINDER not in parts["volatile"]


def test_reminder_names_the_surfaces_it_routes_to(tmp_path, monkeypatch):
    parts = _parts(_agent(), monkeypatch, tmp_path)

    assert "notes/<category>/<topic>.md" in parts["volatile"]
    assert "MEMORY.md" in MEMORY_WRITE_REMINDER
    # The trigger must not become a second, competing contract: it points at the
    # root conventions instead of restating the categories or the read rules.
    assert "work, study, life" not in MEMORY_WRITE_REMINDER


def test_reminder_is_byte_stable_across_builds(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    agent = _agent()

    with patch("agent.system_prompt._skills_prompt", return_value="SKILLS_MARKER"):
        first = build_system_prompt(agent)
        second = build_system_prompt(agent)

    assert first == second
    assert first.endswith(MEMORY_WRITE_REMINDER)
    assert first.count(MEMORY_WRITE_REMINDER) == 1
