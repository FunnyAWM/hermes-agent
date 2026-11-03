"""Tests for the DevicePairingProvider plugin (TODO T-09a / PRD 远 04).

Covers the pairing-code store semantics (mirroring ``gateway/pairing.py``), the token
lifecycle (mint once, verify via ``verify_session``, revoke fails closed), the provider
protocol, and the gated end-to-end chain: ``/auth/password-login`` with a one-time code →
device token → gated REST bearer leg → ``/api/auth/ws-ticket`` → the ``/api/ws?token=``
upgrade leg — plus revocation bouncing back to 401.

The E2E harness mirrors ``test_dashboard_auth_ws_auth.py`` (flip ``app.state.auth_required``,
drive a ``TestClient``); the unit tests run against a throwaway ``HERMES_HOME``.
"""
from __future__ import annotations

import json
import time

import pytest

from fastapi.testclient import TestClient

from hermes_cli import web_server
import hermes_cli.web_server_chat as _web_server_chat
from hermes_cli.dashboard_auth import (
    InvalidCredentialsError, RefreshExpiredError, assert_protocol_compliance, clear_providers,
    get_provider, list_token_providers, register_provider)
from hermes_cli.dashboard_auth.cookies import SESSION_AT_COOKIE
from hermes_cli.dashboard_auth.routes import _reset_password_rate_limit
from hermes_cli.dashboard_auth.ws_tickets import _reset_for_tests, consume_ticket
from plugins.dashboard_auth.device_pairing import (
    ALPHABET, CODE_LENGTH, CODE_TTL_SECONDS, LOCKOUT_SECONDS, MAX_FAILED_ATTEMPTS,
    RATE_LIMIT_SECONDS, DevicePairingProvider, DevicePairingStore)
from tests.hermes_cli.test_dashboard_auth_ws_auth import _fake_ws


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def pairing_home(tmp_path, monkeypatch):
    """Throwaway HERMES_HOME so the store never touches the real profile."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _reset_password_rate_limit()
    return tmp_path


@pytest.fixture
def provider(pairing_home):
    return DevicePairingProvider(store=DevicePairingStore())


@pytest.fixture
def gated_app(provider):
    """web_server.app in gated mode with the device-pairing provider registered."""
    _reset_for_tests()
    clear_providers()
    _reset_password_rate_limit()
    register_provider(provider)
    prev_host = getattr(web_server.app.state, "bound_host", None)
    prev_port = getattr(web_server.app.state, "bound_port", None)
    prev_required = getattr(web_server.app.state, "auth_required", None)
    web_server.app.state.bound_host = "fly-app.fly.dev"
    web_server.app.state.bound_port = 443
    web_server.app.state.auth_required = True
    yield TestClient(web_server.app, base_url="https://fly-app.fly.dev")
    clear_providers()
    _reset_for_tests()
    web_server.app.state.bound_host = prev_host
    web_server.app.state.bound_port = prev_port
    web_server.app.state.auth_required = prev_required


def _paired_code(store: DevicePairingStore, label: str = "phone") -> str:
    code = store.generate_code(label)
    assert code is not None
    return code


def _clear_limits(store: DevicePairingStore) -> None:
    """Drop throttle/lockout state — the per-label 10-minute throttle is production
    behaviour that would otherwise chain-fail multi-code tests in the same home."""
    store._limits_path().write_text("{}", encoding="utf-8")


def _at_token(client: TestClient) -> str:
    """The session access-token cookie value; Secure cookies get the ``__Host-`` prefix."""
    for cookie in client.cookies.jar:
        if cookie.name.endswith(SESSION_AT_COOKIE):
            return str(cookie.value)
    raise AssertionError(f"no {SESSION_AT_COOKIE} cookie in {list(client.cookies.jar)}")


def _expire_all_codes(store: DevicePairingStore) -> None:
    path = store._pending_path()
    pending = json.loads(path.read_text(encoding="utf-8"))
    for entry in pending.values():
        entry["created_at"] = time.time() - CODE_TTL_SECONDS - 1
    path.write_text(json.dumps(pending), encoding="utf-8")


# ---------------------------------------------------------------------------
# Code semantics — mirrored from gateway/pairing.py
# ---------------------------------------------------------------------------

class TestPairingCodeSemantics:
    def test_constants_match_gateway_pairing(self):
        from gateway import pairing as gateway_pairing

        assert ALPHABET == gateway_pairing.ALPHABET
        assert CODE_LENGTH == gateway_pairing.CODE_LENGTH
        assert CODE_TTL_SECONDS == gateway_pairing.CODE_TTL_SECONDS
        assert RATE_LIMIT_SECONDS == gateway_pairing.RATE_LIMIT_SECONDS
        assert LOCKOUT_SECONDS == gateway_pairing.LOCKOUT_SECONDS
        assert MAX_FAILED_ATTEMPTS == gateway_pairing.MAX_FAILED_ATTEMPTS

    def test_code_shape(self, pairing_home):
        code = _paired_code(DevicePairingStore())
        assert len(code) == CODE_LENGTH
        assert set(code) <= set(ALPHABET)

    def test_code_request_throttled_per_label(self, pairing_home):
        store = DevicePairingStore()
        assert store.generate_code("phone") is not None
        assert store.generate_code("phone") is None  # 1 per label per 10 min
        assert store.generate_code("tablet") is not None  # other labels unaffected

    def test_label_mismatch_rejected(self, pairing_home):
        store = DevicePairingStore()
        code = _paired_code(store, "phone")
        assert store.redeem_code("tablet", code) is None
        assert store.redeem_code("phone", code) is not None

    def test_code_single_use(self, pairing_home):
        store = DevicePairingStore()
        code = _paired_code(store, "phone")
        assert store.redeem_code("phone", code) is not None
        assert store.redeem_code("phone", code) is None

    def test_expired_code_rejected(self, pairing_home):
        store = DevicePairingStore()
        code = _paired_code(store, "phone")
        _expire_all_codes(store)
        assert store.redeem_code("phone", code) is None

    def test_lockout_after_five_failures(self, pairing_home):
        store = DevicePairingStore()
        code = _paired_code(store, "phone")
        for _ in range(MAX_FAILED_ATTEMPTS):
            assert store.redeem_code("phone", "WRONG000") is None
        assert store.locked_out() is True
        # Even the VALID code stays unusable while locked out.
        assert store.redeem_code("phone", code) is None

    def test_success_resets_failure_streak(self, pairing_home):
        store = DevicePairingStore()
        for _ in range(MAX_FAILED_ATTEMPTS - 1):
            store.redeem_code("phone", "WRONG000")
        assert store.redeem_code("phone", _paired_code(store, "phone")) is not None
        for _ in range(MAX_FAILED_ATTEMPTS - 1):
            assert store.redeem_code("phone", "WRONG000") is None
        assert store.locked_out() is False


# ---------------------------------------------------------------------------
# Token lifecycle
# ---------------------------------------------------------------------------

class TestTokenLifecycle:
    def test_redeem_mints_verifiable_token(self, provider):
        store = provider._store
        code = _paired_code(store, "pixel-9")
        token = store.redeem_code("pixel-9", code)
        assert token and len(token) >= 32
        session = provider.verify_session(access_token=token)
        assert session is not None
        assert session.user_id == "pixel-9"
        assert session.provider == "device-pairing"
        assert session.access_token == token
        assert session.refresh_token == ""
        assert session.expires_at > time.time()

    def test_plaintext_token_never_at_rest(self, provider):
        store = provider._store
        token = store.redeem_code("pixel-9", _paired_code(store, "pixel-9"))
        tokens_blob = store._tokens_path().read_text(encoding="utf-8")
        pending_blob = store._pending_path().read_text(encoding="utf-8")
        assert token not in tokens_blob
        assert token not in pending_blob

    def test_unknown_and_blank_tokens_rejected(self, provider):
        assert provider.verify_session(access_token="never-minted") is None
        assert provider.verify_session(access_token="") is None

    def test_expired_token_rejected(self, provider):
        store = provider._store
        token = store.redeem_code("pixel-9", _paired_code(store, "pixel-9"))
        path = store._tokens_path()
        tokens = json.loads(path.read_text(encoding="utf-8"))
        for entry in tokens.values():
            entry["expires_at"] = time.time() - 1
        path.write_text(json.dumps(tokens), encoding="utf-8")
        assert provider.verify_session(access_token=token) is None

    def test_revoke_by_label_and_by_id(self, provider):
        store = provider._store
        token = store.redeem_code("pixel-9", _paired_code(store, "pixel-9"))
        assert store.revoke("pixel-9") is True
        assert provider.verify_session(access_token=token) is None
        assert store.revoke("pixel-9") is False

        _clear_limits(store)  # second code for the same label: skip the 10-min throttle
        token = store.redeem_code("pixel-9", _paired_code(store, "pixel-9"))
        device_id = store.list_devices()[0]["id"]
        assert store.revoke(device_id) is True
        assert provider.verify_session(access_token=token) is None
        assert store.revoke("no-such-device") is False


# ---------------------------------------------------------------------------
# Provider protocol
# ---------------------------------------------------------------------------

class TestProviderProtocol:
    def test_protocol_compliance(self, provider):
        assert_protocol_compliance(DevicePairingProvider)

    def test_bad_code_is_generic_invalid_credentials(self, provider):
        with pytest.raises(InvalidCredentialsError):
            provider.complete_password_login(username="pixel-9", password="WRONG000")

    def test_device_tokens_are_not_token_route_credentials(self, provider):
        assert provider.supports_token is False
        assert provider not in list_token_providers()

    def test_no_refresh(self, provider):
        with pytest.raises(RefreshExpiredError):
            provider.refresh_session(refresh_token="")

    def test_register_skips_until_config_opts_in(self, pairing_home):
        seen = {}

        class _Ctx:
            def register_dashboard_auth_provider(self, provider):
                seen["provider"] = provider
                register_provider(provider)  # the real PluginContext path

            def register_cli_command(self, **kwargs):
                seen["cli"] = kwargs["name"]

        from plugins.dashboard_auth import device_pairing

        try:
            device_pairing.register(_Ctx())
            assert "provider" not in seen
            assert "enabled" in device_pairing.LAST_SKIP_REASON

            (pairing_home / "config.yaml").write_text(
                "dashboard:\n  device_pairing:\n    enabled: true\n", encoding="utf-8")
            device_pairing.register(_Ctx())
            assert seen["provider"].name == "device-pairing"
            assert seen["cli"] == "device-pair"
            # The registered provider is discoverable by the password-login route.
            assert get_provider("device-pairing") is seen["provider"]
        finally:
            clear_providers()


# ---------------------------------------------------------------------------
# Gated end-to-end: code -> token -> REST bearer -> ws-ticket / ws token leg
# ---------------------------------------------------------------------------

class TestGatedEndToEnd:
    def test_no_credential_is_401(self, gated_app):
        assert gated_app.get("/api/auth/me").status_code == 401
        assert gated_app.post("/api/auth/ws-ticket").status_code == 401

    def test_pair_then_use_bearer(self, gated_app, provider):
        store = provider._store
        code = _paired_code(store, "pixel-9")
        response = gated_app.post("/auth/password-login", json={
            "provider": "device-pairing", "username": "pixel-9", "password": code})
        assert response.status_code == 200, response.text
        assert response.json()["ok"] is True
        # The minted token rode back as the session access-token cookie.
        token = _at_token(gated_app)

        me = gated_app.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["user_id"] == "pixel-9"
        assert me.json()["provider"] == "device-pairing"
        session = provider.verify_session(access_token=token)
        assert session is not None and session.user_id == "pixel-9"

    def test_bearer_leg_mints_ws_ticket(self, gated_app, provider):
        store = provider._store
        code = _paired_code(store, "pixel-9")
        assert gated_app.post("/auth/password-login", json={
            "provider": "device-pairing", "username": "pixel-9", "password": code,
        }).status_code == 200
        token = _at_token(gated_app)

        fresh = TestClient(web_server.app, base_url="https://fly-app.fly.dev")
        rejected = fresh.get("/api/auth/me")
        assert rejected.status_code == 401
        minted = fresh.post("/api/auth/ws-ticket", headers={"Authorization": f"Bearer {token}"})
        assert minted.status_code == 200, minted.text
        info = consume_ticket(minted.json()["ticket"])
        assert info["user_id"] == "pixel-9"
        assert info["provider"] == "device-pairing"

    def test_ws_upgrade_token_leg(self, gated_app, provider):
        store = provider._store
        token = store.redeem_code("pixel-9", _paired_code(store, "pixel-9"))
        ws = _fake_ws(query={"token": token}, path="/api/ws")
        assert _web_server_chat._ws_auth_ok(ws) is True
        assert ws._hermes_auth_identity == {"user_id": "pixel-9", "provider": "device-pairing"}

    def test_revocation_bounces_back_to_401(self, gated_app, provider):
        store = provider._store
        token = store.redeem_code("pixel-9", _paired_code(store, "pixel-9"))
        fresh = TestClient(web_server.app, base_url="https://fly-app.fly.dev")
        assert fresh.get("/api/auth/me", headers={
            "Authorization": f"Bearer {token}"}).status_code == 200

        assert store.revoke("pixel-9") is True
        assert fresh.get("/api/auth/me", headers={
            "Authorization": f"Bearer {token}"}).status_code == 401
        ws = _fake_ws(query={"token": token}, path="/api/ws")
        reason, credential = _web_server_chat._ws_auth_reason(ws)
        assert (reason, credential) == ("token_invalid", "token")
