"""Notes organizer (Android 记 06): runs after a delivered answer, marker-recovered, notes-only."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from agent import notes_organizer
from hermes_notes.settings import DEFAULTS, NotesSettings
from tools.file_tools_write_guards import _check_notes_write_scope, bind_notes_write_scope


def _settings(organize_turns: int, unused_turns: int = 10) -> NotesSettings:
    return NotesSettings(
        soft_limit=DEFAULTS["notes_soft_limit"],
        hard_limit=None,
        remove_expired_notes=True,
        document_unused_turns=unused_turns,
        organize_turns=organize_turns,
    )


def _agent(turns: int) -> SimpleNamespace:
    return SimpleNamespace(_user_turn_count=turns, _background_review_agent=None)


@pytest.fixture
def settings(monkeypatch):
    def apply(*, organize_turns: int, unused_turns: int = 10):
        current = _settings(organize_turns, unused_turns)
        monkeypatch.setattr("hermes_notes.settings.load_notes_settings", lambda: current)
        return current

    return apply


def test_organizer_is_off_by_default_and_fires_on_interval(tmp_path, monkeypatch, settings):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "notes" / "work").mkdir(parents=True)
    settings(organize_turns=0)
    assert notes_organizer.should_organize(_agent(turns=5)) is False
    settings(organize_turns=5)
    assert notes_organizer.should_organize(_agent(turns=4)) is False
    assert notes_organizer.should_organize(_agent(turns=5)) is True
    assert notes_organizer.should_organize(_agent(turns=10)) is True
    assert notes_organizer.organizer_settings(_agent(turns=5)) == 5
    settings(organize_turns=0)
    assert notes_organizer.organizer_settings(_agent(turns=5)) is None


def test_organizer_skips_while_review_in_flight_and_without_notes(tmp_path, monkeypatch, settings):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "notes").mkdir()
    settings(organize_turns=5)
    busy = _agent(turns=5)
    busy._background_review_agent = object()
    assert notes_organizer.should_organize(busy) is False

    empty = tmp_path / "empty-home"
    empty.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(empty))
    assert notes_organizer.should_organize(_agent(turns=5)) is False


def test_maybe_spawn_starts_a_context_bound_thread_only_when_due(tmp_path, monkeypatch, settings):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "notes").mkdir()
    started: list[object] = []

    class _Thread:
        daemon = True

        def start(self):
            started.append(self)

    monkeypatch.setattr(
        "agent.memory_provider.spawn_context_thread", lambda target, *, name, args: _Thread())
    settings(organize_turns=5)
    assert notes_organizer.maybe_spawn(_agent(turns=4)) is False
    assert started == []
    assert notes_organizer.maybe_spawn(_agent(turns=5)) is True
    assert len(started) == 1


def test_notes_write_scope_allows_only_category_markdown(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    allowed = tmp_path / "notes" / "work" / "plan.md"
    allowed.parent.mkdir(parents=True)
    forbidden = [
        tmp_path / "memories" / "USER.md",
        tmp_path / "notes" / "docs" / "spec.md",
        tmp_path / "notes" / "work" / "plan.txt",
        tmp_path / "SOUL.md",
    ]
    for path in forbidden:
        path.parent.mkdir(parents=True, exist_ok=True)
    assert _check_notes_write_scope(str(allowed)) is None  # outside the scope: guard is inert
    with bind_notes_write_scope():
        assert _check_notes_write_scope(str(allowed)) is None
        for path in forbidden:
            assert _check_notes_write_scope(str(path)) is not None, path
    assert _check_notes_write_scope(str(forbidden[0])) is None


def test_incomplete_marker_recovers_on_any_turn(tmp_path, monkeypatch, settings):
    """Android 记 06: a failed pass is retried on any later turn, not just the next multiple."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "notes").mkdir()
    settings(organize_turns=5)
    due = _agent(turns=6)
    assert notes_organizer.should_organize(due) is False  # between intervals
    marker = tmp_path / "notes" / ".organize-incomplete"
    marker.write_text("incomplete", encoding="utf-8")
    assert notes_organizer.should_organize(due) is True
    assert notes_organizer.should_organize(_agent(turns=7)) is True
    settings(organize_turns=0)
    assert notes_organizer.should_organize(_agent(turns=7)) is False  # recovery needs the interval
    settings(organize_turns=5)
    due._notes_organizer_in_flight = True
    assert notes_organizer.should_organize(due) is False  # a pass in flight owns the marker


def test_organizer_marks_before_a_pass_and_clears_it_only_on_success(tmp_path, monkeypatch, settings):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "notes").mkdir()
    settings(organize_turns=5)
    marker = tmp_path / "notes" / ".organize-incomplete"
    marked_during_pass: list[bool] = []

    class _Fork:
        _session_messages: list = []

        def run_conversation(self, *, user_message, conversation_history):
            marked_during_pass.append(marker.is_file())
            return {"final_response": "no changes"}

    monkeypatch.setattr(
        "agent.background_review.build_cache_parity_fork",
        lambda agent, **kwargs: (_Fork(), None, None))
    monkeypatch.setattr("agent.background_review._release_fork_clients", lambda fork: None)
    agent = _agent(turns=5)
    notes_organizer._run_notes_organizer(agent)
    assert marked_during_pass == [True]  # marked before the pass ran
    assert not marker.exists()  # a completed pass clears it
    assert agent._notes_organizer_in_flight is False


def test_organizer_writes_the_memory_block_with_a_cas_baseline(tmp_path, monkeypatch, settings):
    """Android MemoryOrganizer: the pass rewrites MEMORY.md from its final text."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "notes").mkdir()
    (tmp_path / "memories").mkdir()
    memory = tmp_path / "memories" / "MEMORY.md"
    memory.write_text("old entry\n", encoding="utf-8")
    settings(organize_turns=5)

    class _Fork:
        _session_messages: list = []

        def run_conversation(self, *, user_message, conversation_history):
            return {"final_response": "no changes\n---MEMORY---\n- project uses uv\n"}

    monkeypatch.setattr(
        "agent.background_review.build_cache_parity_fork",
        lambda agent, **kwargs: (_Fork(), None, None))
    monkeypatch.setattr("agent.background_review._release_fork_clients", lambda fork: None)
    notes_organizer._run_notes_organizer(_agent(turns=5))
    assert memory.read_text(encoding="utf-8").strip() == "- project uses uv"


def test_memory_write_is_skipped_when_the_file_changed_during_the_pass(tmp_path, monkeypatch, settings):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "notes").mkdir()
    (tmp_path / "memories").mkdir()
    memory = tmp_path / "memories" / "MEMORY.md"
    memory.write_text("old entry\n", encoding="utf-8")
    settings(organize_turns=5)

    class _Fork:
        _session_messages: list = []

        def run_conversation(self, *, user_message, conversation_history):
            memory.write_text("edited by the user mid-pass\n", encoding="utf-8")
            return {"final_response": "---MEMORY---\nclobbered"}

    monkeypatch.setattr(
        "agent.background_review.build_cache_parity_fork",
        lambda agent, **kwargs: (_Fork(), None, None))
    monkeypatch.setattr("agent.background_review._release_fork_clients", lambda fork: None)
    notes_organizer._run_notes_organizer(_agent(turns=5))
    assert memory.read_text(encoding="utf-8").strip() == "edited by the user mid-pass"


def test_failed_pass_keeps_the_marker_for_the_next_turn(tmp_path, monkeypatch, settings):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "notes").mkdir()
    settings(organize_turns=5)
    marker = tmp_path / "notes" / ".organize-incomplete"

    class _Fork:
        def run_conversation(self, *, user_message, conversation_history):
            raise RuntimeError("provider unavailable")

    monkeypatch.setattr(
        "agent.background_review.build_cache_parity_fork",
        lambda agent, **kwargs: (_Fork(), None, None))
    monkeypatch.setattr("agent.background_review._release_fork_clients", lambda fork: None)
    agent = _agent(turns=5)
    notes_organizer._run_notes_organizer(agent)  # must not raise out of the thread body
    assert marker.is_file()
    assert agent._notes_organizer_in_flight is False
