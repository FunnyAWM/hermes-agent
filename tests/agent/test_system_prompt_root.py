"""PRD §7.5 / D9: the memory root conventions lead the stable tier."""

from types import SimpleNamespace
from unittest.mock import patch

from agent.system_prompt import MEMORY_ROOT_PROMPT, build_system_prompt, build_system_prompt_parts


def _agent(**overrides):
    base = dict(
        load_soul_identity=True,
        skip_context_files=False,
        valid_tool_names=[],
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


def test_root_conventions_precede_soul_identity():
    with patch("agent.prompt_builder.load_soul_md", return_value="SOUL_MARKER"):
        parts = build_system_prompt_parts(_agent())

    stable = parts["stable"]
    assert stable.startswith(MEMORY_ROOT_PROMPT)
    assert stable.index(MEMORY_ROOT_PROMPT) < stable.index("SOUL_MARKER")


def test_root_conventions_survive_empty_memory_and_no_soul():
    parts = build_system_prompt_parts(_agent(load_soul_identity=False, skip_context_files=True))
    assert parts["stable"].startswith(MEMORY_ROOT_PROMPT)
    assert "notes/docs/" in MEMORY_ROOT_PROMPT


def test_memory_order_is_memory_user_notes_after_root(tmp_path, monkeypatch):
    note = tmp_path / "notes" / "work" / "plan.md"
    note.parent.mkdir(parents=True)
    note.write_text("---\nname: 计划\ndescription: 发布\ndocs: no\n---\n", encoding="utf-8")
    memory = SimpleNamespace(format_for_system_prompt=lambda target: {"memory": "MEMORY_MARKER", "user": "USER_MARKER"}.get(target))
    agent = _agent(_memory_store=memory, _memory_enabled=True, _user_profile_enabled=True)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    with patch("agent.system_prompt._skills_prompt", return_value="SKILLS_MARKER"):
        full = "\n\n".join(build_system_prompt_parts(agent).values())

    assert full.index(MEMORY_ROOT_PROMPT) < full.index("MEMORY_MARKER") < full.index("USER_MARKER") < full.index("NOTES INDEX")


def test_task_routing_guidance_needs_both_task_tools():
    both = build_system_prompt_parts(_agent(valid_tool_names=["todo_list", "cronjob_manage"]))["stable"]
    assert "session task list" in both
    assert "cronjob_manage" in both

    only_tasks = build_system_prompt_parts(_agent(valid_tool_names=["todo_list"]))["stable"]
    assert "session task list" not in only_tasks


def test_stable_prefix_is_byte_stable_across_calls():
    agent = _agent(load_soul_identity=False, skip_context_files=True)
    first = build_system_prompt(agent)
    second = build_system_prompt(agent)
    assert first == second
