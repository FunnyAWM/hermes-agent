"""The web QR flow configures only the profile that requested the pairing."""

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

import hermes_yaml as yaml


@pytest.mark.asyncio
@pytest.mark.parametrize("already_configured", [False, True])
async def test_scan_confirms_and_activates_only_the_owning_profile(tmp_path, monkeypatch, already_configured):
    from gateway.platform_registry import PlatformEntry, PlatformRegistry
    from hermes_cli.web_routers import messaging, messaging_weixin as api
    from hermes_cli.web_server import _SESSION_HEADER_NAME, _SESSION_TOKEN, app
    from plugins.platforms.weixin import adapter

    home = tmp_path / "home"
    work = home / "profiles" / "work"
    work.mkdir(parents=True)
    (home / "config.yaml").write_text("{}\n", encoding="utf8")
    (work / "config.yaml").write_text("{}\n", encoding="utf8")
    if already_configured:
        (work / ".env").write_text("WEIXIN_ALLOWED_USERS=previous-owner\n", encoding="utf8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    shown, scanned = asyncio.Event(), asyncio.Event()

    async def ilink(_session, *, endpoint, **_opts):
        if endpoint.startswith(adapter.EP_GET_BOT_QR):
            return {"qrcode": "private-poll-key", "qrcode_img_content": "https://weixin.example/scan-me"}
        shown.set()
        await scanned.wait()
        return {"status": "confirmed", "ilink_bot_id": "test@im.bot", "bot_token": "test-only-token",
                "ilink_user_id": "owner", "baseurl": adapter.ILINK_BASE_URL}

    monkeypatch.setattr(adapter, "_api_get", ilink)
    registry = PlatformRegistry()
    ctx = SimpleNamespace(register_platform=lambda **kwargs: registry.register(PlatformEntry(**kwargs)))
    monkeypatch.setattr(api, "discover_plugins", lambda: adapter.register(ctx))
    monkeypatch.setattr(api, "platform_registry", registry)
    hot_serve = Mock(return_value=not already_configured)
    restart = Mock(return_value={"restart_started": False})
    monkeypatch.setattr(api, "_restart_gateway_after", restart)
    monkeypatch.setattr(messaging, "_notify_multiplexer_hot_serve", hot_serve)

    url = "/api/messaging/weixin/onboarding"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost",
                                 headers={_SESSION_HEADER_NAME: _SESSION_TOKEN}) as client:
        started = await client.post(f"{url}/start?profile=work")
        assert started.status_code == 200, started.text
        path = f"{url}/{started.json()['pairing_id']}"
        try:
            await asyncio.wait_for(shown.wait(), 5)
            status = await client.get(path, params={"profile": "work"})
            assert status.json()["qr_payload"] == "https://weixin.example/scan-me"
            assert "private-poll-key" not in status.text
            assert (await client.get(path)).status_code == 404
            assert (await client.delete(path)).status_code == 404
        finally:
            scanned.set()
        await asyncio.wait_for(api._pairings[work].task, 10)
        done = await client.get(path, params={"profile": "work"})
        assert done.json()["status"] == "connected", done.text
        assert done.json()["needs_restart"] is already_configured
        assert "test-only-token" not in done.text
    assert "WEIXIN_TOKEN=test-only-token" in (work / ".env").read_text(encoding="utf8")
    allowed = "previous-owner" if already_configured else "owner"
    assert f"WEIXIN_ALLOWED_USERS={allowed}" in (work / ".env").read_text(encoding="utf8")
    assert yaml.safe_load((work / "config.yaml").read_text("utf8"))["platforms"]["weixin"]["enabled"]
    assert not (home / ".env").exists()
    hot_serve.assert_called_once_with("work")
    if already_configured:
        restart.assert_called_once_with("work", what="Weixin onboarding", label="Weixin")
    else:
        restart.assert_not_called()
