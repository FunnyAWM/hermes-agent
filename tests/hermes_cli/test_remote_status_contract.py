"""T-09c: the PC side of remote mode is a trustworthy status/auth contract.

The Android client cannot be built yet (M5), so these tests pin what the phone will rely on:
``/api/status`` stays public, a gated deployment answers a structured 401, and the WebSocket gate
uses 4401 (bad credential) / 4403 (host or origin mismatch).
"""

from __future__ import annotations


import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from hermes_cli import web_server
from hermes_cli.dashboard_auth import clear_providers, register_provider
from tests.hermes_cli.conftest_dashboard_auth import StubAuthProvider


@pytest.fixture
def gated(monkeypatch):
    clear_providers()
    register_provider(StubAuthProvider())
    monkeypatch.setattr(web_server.app.state, "auth_required", True, raising=False)
    monkeypatch.setattr(web_server, "_DASHBOARD_EMBEDDED_CHAT_ENABLED", True)
    try:
        yield TestClient(web_server.app)
    finally:
        clear_providers()


def test_default_bind_stays_loopback(monkeypatch):
    from unittest.mock import Mock
    from hermes_cli import nous_auth_keepalive

    gate = Mock(side_effect=RuntimeError("stop before binding"))
    monkeypatch.setattr(nous_auth_keepalive, "start_nous_auth_keepalive", lambda: None)
    monkeypatch.setattr(web_server, "_configure_auth_gate", gate)
    with pytest.raises(RuntimeError, match="stop before binding"):
        web_server.start_server()
    gate.assert_called_once_with("127.0.0.1", False, None, None)


def test_status_stays_public_with_an_origin_header(gated):
    response = gated.get("/api/status", headers={"Origin": "http://192.168.1.50"})
    assert response.status_code == 200, response.text
    assert isinstance(response.json(), dict)


def test_protected_rest_without_credentials_is_a_structured_401(gated):
    response = gated.get("/api/profiles")
    assert response.status_code == 401, response.text
    body = response.json()
    assert isinstance(body, dict) and body.get("reason"), body


def test_websocket_without_credentials_closes_4401(gated):
    with pytest.raises(WebSocketDisconnect) as rejected:
        with gated.websocket_connect("/api/ws"):
            pass
    assert rejected.value.code == 4401


def test_websocket_with_foreign_host_closes_4403(monkeypatch):
    monkeypatch.setattr(web_server.app.state, "auth_required", False, raising=False)
    monkeypatch.setattr(web_server.app.state, "bound_host", "127.0.0.1", raising=False)
    monkeypatch.setattr(web_server.app.state, "bound_port", 9119, raising=False)
    monkeypatch.setattr(web_server, "_DASHBOARD_EMBEDDED_CHAT_ENABLED", True)
    client = TestClient(web_server.app)
    with pytest.raises(WebSocketDisconnect) as rejected:
        with client.websocket_connect(
            f"/api/events?token={web_server._SESSION_TOKEN}&channel=remote-contract",
            headers={"Host": "evil.example", "Origin": "http://evil.example"},
        ):
            pass
    assert rejected.value.code == 4403
