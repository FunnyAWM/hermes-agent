"""Conversation window semantics (agent/session_window.py) and their wiring into the
request copy built by ``build_api_messages``.

Ported from KouriChat Next (Android) 设 08: the sliding window caps how many user turns
reach the model; the frozen window keeps the last Y turns as a stable prefix until the
window refills to X.
"""

from __future__ import annotations

from types import SimpleNamespace

from agent.session_window import (
    kept_turn_count,
    request_start_index,
    user_turn_count,
    window_settings,
)


def _user(text: str) -> dict:
    return {"role": "user", "content": text}


def _assistant(text: str) -> dict:
    return {"role": "assistant", "content": text}


def _tool(call_id: str) -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": "ok"}


def _turns(count: int) -> list[dict]:
    history: list[dict] = []
    for index in range(count):
        history.append(_user(f"turn {index}"))
        history.append(_assistant(f"reply {index}"))
    return history


def test_settings_default_to_off() -> None:
    assert window_settings({}) == (0, 0)
    assert window_settings(None) == (0, 0)
    assert window_settings({"context": "broken"}) == (0, 0)


def test_settings_read_positive_turns_and_strings() -> None:
    assert window_settings({"context": {"session_window_turns": 6}}) == (6, 0)
    assert window_settings({"context": {"session_window_turns": "6", "frozen_window_turns": "2"}}) == (6, 2)


def test_invalid_frozen_window_falls_back_to_plain_sliding() -> None:
    # Y must be 1 <= Y < X; anything else disables the freeze but keeps the window.
    assert window_settings({"context": {"session_window_turns": 5, "frozen_window_turns": 5}}) == (5, 0)
    assert window_settings({"context": {"session_window_turns": 5, "frozen_window_turns": 9}}) == (5, 0)
    assert window_settings({"context": {"frozen_window_turns": 2}}) == (0, 0)


def test_non_numeric_and_boolean_values_are_off() -> None:
    assert window_settings({"context": {"session_window_turns": True}}) == (0, 0)
    assert window_settings({"context": {"session_window_turns": -4, "frozen_window_turns": "x"}}) == (0, 0)


def test_turn_count_ignores_non_user_rows() -> None:
    history = [_user("a"), _assistant("b"), _tool("c1"), _user("c")]
    assert user_turn_count(history) == 2


def test_plain_window_keeps_latest_turns() -> None:
    assert kept_turn_count(total=2, window=4, frozen=0) == 2
    assert kept_turn_count(total=4, window=4, frozen=0) == 4
    assert kept_turn_count(total=9, window=4, frozen=0) == 4


def test_frozen_window_refreezes_on_the_same_schedule_as_android() -> None:
    # X = 6, Y = 2: freeze to 2 at turn 6, append until turn 10, then re-freeze to 2.
    schedule = [kept_turn_count(total, 6, 2) for total in range(1, 13)]
    assert schedule[:5] == [1, 2, 3, 4, 5]
    assert schedule[5:] == [2, 3, 4, 5, 2, 3, 4]


def test_start_index_cuts_at_a_user_row_and_keeps_tool_rows() -> None:
    history = [
        _user("one"), _assistant("r1"),
        _user("two"), {"role": "assistant", "content": "", "tool_calls": [{"id": "c2"}]},
        _tool("c2"), _assistant("r2"),
        _user("three"), _assistant("r3"),
    ]
    start = request_start_index(history, window_turns=2, frozen_window_turns=0)
    assert history[start] == _user("two")
    assert [row.get("tool_call_id") for row in history[start:]].count("c2") == 1


def test_no_window_keeps_everything() -> None:
    assert request_start_index(_turns(9), window_turns=0, frozen_window_turns=0) == 0
    assert request_start_index([], window_turns=4, frozen_window_turns=2) == 0


class _WireAgent(SimpleNamespace):
    """Minimal agent surface ``build_api_messages`` reads; unknown optional attributes read falsy."""

    _current_turn_timestamp = 1.0
    ephemeral_system_prompt = ""
    api_mode = "chat_completions"
    provider = "test"
    model = "test"
    base_url = ""
    tools = None
    _usage_anchor = None

    def __init__(self, window: int, frozen: int) -> None:
        super().__init__(_session_window_turns=window, _frozen_window_turns=frozen)

    def __getattr__(self, name: str):
        return None

    def _copy_reasoning_content_for_api(self, msg, api_msg) -> None:
        return None

    def _should_sanitize_tool_calls(self) -> bool:
        return False


def _wire_agent(window: int, frozen: int) -> _WireAgent:
    return _WireAgent(window, frozen)


def test_build_api_messages_applies_the_window_to_the_wire_copy_only() -> None:
    from agent.turn_context import build_api_messages

    history = _turns(5)
    agent = _wire_agent(2, 0)
    wire, _system = build_api_messages(
        agent, history, current_turn_user_idx=8,
        ext_prefetch_cache="", plugin_user_context="", moa_config=None,
        active_system_prompt="fixed prefix",
    )

    conversation = [row for row in wire if row.get("role") != "system"]
    assert conversation[0] == _user("turn 3")
    assert len(conversation) == 4
    # The transcript the agent persists is untouched.
    assert len(history) == 10


def test_build_api_messages_frozen_prefix_grows_then_refreezes() -> None:
    from agent.turn_context import build_api_messages

    def first_user(history: list[dict]) -> dict:
        wire, _system = build_api_messages(
            _wire_agent(6, 2), history, current_turn_user_idx=len(history) - 2,
            ext_prefetch_cache="", plugin_user_context="", moa_config=None,
            active_system_prompt="fixed prefix",
        )
        return next(row for row in wire if row.get("role") != "system")

    # X = 6, Y = 2: at 6 turns the prefix freezes to the last 2, grows one turn at a
    # time until 10, then re-freezes — the wire copy follows the same schedule.
    assert first_user(_turns(6)) == _user("turn 4")
    assert first_user(_turns(7)) == _user("turn 4")
    assert first_user(_turns(9)) == _user("turn 4")
    assert first_user(_turns(10)) == _user("turn 8")


def test_build_api_messages_never_drops_the_current_turn() -> None:
    from agent.turn_context import build_api_messages

    history = _turns(4)
    agent = _wire_agent(1, 0)
    # A retry/continue can point at an older user row; the window must not cut past it.
    wire, _system = build_api_messages(
        agent, history, current_turn_user_idx=2,
        ext_prefetch_cache="", plugin_user_context="", moa_config=None,
        active_system_prompt="fixed prefix",
    )

    conversation = [row for row in wire if row.get("role") != "system"]
    assert conversation[0] == _user("turn 1")
    assert any(row.get("content") == "turn 1" for row in conversation)
