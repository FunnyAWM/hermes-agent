"""``sync.disconnect`` / ``sync.reset_chain`` discard pairing state: on a multiplexing backend they
must name their target profile (the REST side's ``destructive_profile`` in
hermes_cli/web_routers/kourichat_link.py) instead of silently acting on the launch profile."""

from __future__ import annotations

import pytest

from tui_gateway import server
from tui_gateway import launch_profile_policy as lpp


@pytest.fixture
def recorder(tmp_path, monkeypatch):
    """A hermes home with a live secondary ``profiles/b`` and a stubbed sync manager."""
    home = tmp_path / "hermes_home"
    b = home / "profiles" / "b"
    for target in (home, b):
        target.mkdir(parents=True)
        (target / "config.yaml").write_text("", encoding="utf-8")
        (target / ".env").write_text("", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(server, "_hermes_home", home)
    monkeypatch.setattr(server, "_served_profile_homes", set())
    monkeypatch.setattr(lpp, "_snapshot", None)
    from agent import secret_scope
    monkeypatch.setattr(secret_scope, "_MULTIPLEX_ACTIVE", False)

    calls: list[str] = []

    class _Manager:
        def disconnect_and_clear(self) -> None:
            calls.append("disconnect")

        def reset_chain(self) -> None:
            calls.append("reset")

    monkeypatch.setattr("hermes_cli.sync_cmd.shared_manager", lambda *args, **kwargs: _Manager())
    return calls


def test_unnamed_destructive_routes_refuse_under_multiplex(recorder, monkeypatch):
    from agent import secret_scope
    monkeypatch.setattr(secret_scope, "_MULTIPLEX_ACTIVE", True)
    for route in ("sync.disconnect", "sync.reset_chain"):
        response = server._methods[route]("rid", {})
        assert "error" not in response, response
        assert response["result"]["ok"] is False
        assert response["result"]["code"] == "PROFILE_REQUIRED"
        assert response["result"]["message"].startswith(route + " requires an explicit profile")
    assert recorder == []


def test_named_profile_runs_the_destructive_route(recorder):
    response = server._methods["sync.disconnect"]("rid", {"profile": "b"})
    assert response["result"] == {"ok": True}
    assert recorder == ["disconnect"]


def test_single_profile_host_still_defaults_to_the_launch_profile(recorder):
    response = server._methods["sync.reset_chain"]("rid", {})
    assert response["result"] == {"ok": True}
    assert recorder == ["reset"]
