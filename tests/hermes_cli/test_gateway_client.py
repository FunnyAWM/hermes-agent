"""Managed-session gateway client contracts."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_connect_gateway_requests_managed_session_for_the_router(monkeypatch, tmp_path):
    from hermes_cli import gateway_client, gateway_runtime
    import hermes_constants
    import websockets.asyncio.client

    endpoint = gateway_runtime.GatewayEndpoint(
        str(tmp_path), "owner", 1, 1, "http://127.0.0.1:1", "none", frozenset()
    )
    requested = {}

    def ticket(home, selected, **policy):
        requested.update(home=home, endpoint=selected, policy=policy)
        return "ticket"

    @asynccontextmanager
    async def connect(*_args, **_kwargs):
        yield SimpleNamespace(
            name="managed-router", subprotocol=gateway_client.GATEWAY_WS_PROTOCOL
        )

    monkeypatch.delenv("HERMES_TUI_GATEWAY_URL", raising=False)
    monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(gateway_runtime, "gateway_profile_home", lambda home: home)
    monkeypatch.setattr(gateway_client, "_session_ticket", ticket)
    monkeypatch.setattr(websockets.asyncio.client, "connect", connect)

    async with gateway_client.connect_gateway(
        endpoint=endpoint, subject="plugin:super_agent", purpose="managed-session"
    ) as client:
        assert client.websocket.name == "managed-router"

    assert requested == {
        "home": tmp_path,
        "endpoint": endpoint,
        "policy": {"subject": "plugin:super_agent", "purpose": "managed-session"},
    }
