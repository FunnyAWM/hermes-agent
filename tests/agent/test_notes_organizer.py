"""Notes organizer (Android 整理轮次): default off, turn-gated, notes-only write scope."""

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
