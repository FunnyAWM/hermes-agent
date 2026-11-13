"""memory.* notes settings load from config.yaml, with safe fallbacks (Android 设 13 parity)."""

from hermes_notes.settings import DEFAULTS, load_notes_settings


def _write_config(home, memory_block: str) -> None:
    (home / "config.yaml").write_text(f"memory:\n{memory_block}", encoding="utf-8")


def test_defaults_when_keys_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    settings = load_notes_settings()
    assert settings.soft_limit == DEFAULTS["notes_soft_limit"] == 128
    assert settings.hard_limit is None
    assert settings.remove_expired_notes is True
    assert settings.document_unused_turns == 10
    assert settings.organize_turns == 0


def test_configured_values_apply_and_bad_values_fall_back(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _write_config(tmp_path, "  notes_soft_limit: 40\n  notes_hard_limit: 60\n"
                            "  remove_expired_notes: false\n  document_unused_turns: 3\n"
                            "  organize_turns: 7\n")
    settings = load_notes_settings()
    assert (settings.soft_limit, settings.hard_limit) == (40, 60)
    assert settings.remove_expired_notes is False
    assert settings.document_unused_turns == 3
    assert settings.organize_turns == 7

    _write_config(tmp_path, "  notes_soft_limit: 0\n  notes_hard_limit: 5\n  organize_turns: -4\n")
    settings = load_notes_settings()
    assert settings.soft_limit == 128 and settings.hard_limit is None
    assert settings.organize_turns == 0
