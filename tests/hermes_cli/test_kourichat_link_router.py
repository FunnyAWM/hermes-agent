"""Tests for ``hermes_cli.web_routers.kourichat_link`` — the dashboard's KouriChat
Link REST surface.

The manager semantics live in ``tests/sync/``; this file pins the router plumbing:
the status payload shape, the HM-600x → 4xx envelope mapping, and the
already-paired conflict. A minimal FastAPI app carries only this router so the
test never pays for the full dashboard server.
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import hermes_cli.web_routers.kourichat_link as link_routes
from hermes_cli.sync_cmd import SyncSettings
from sync.errors import SyncError, SyncErrorCode
from sync.manager import SyncStatus


class FakeManager:
    def __init__(self, *, error: SyncError | None = None, paired: bool = False) -> None:
        self._error = error
        self._paired = paired
        self.serving = False

    def status(self) -> SyncStatus:
        if self._error is not None:
            raise self._error
        return SyncStatus(
            role="secondary" if self._paired else "disabled",
            paired=self._paired,
            device_id="d" * 32 if self._paired else None,
            master_endpoint="192.168.1.20:27890" if self._paired else "",
            pinned_fingerprint="f" * 64 if self._paired else None,
            tracked_files=3 if self._paired else 0,
            syncable_files=3,
        )

    def create_chain(self):
        if self._error is not None:
            raise self._error
        raise ValueError("already paired; disconnect or reset first")

    def join_chain(self, words, endpoint):
        raise self._error

    def sync_now(self):
        raise self._error

    def start_server(self):
        raise self._error

    def stop_server(self) -> None:
        return None

    def disconnect_and_clear(self) -> None:
        return None

    def reset_chain(self) -> None:
        return None


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(link_routes.router)
    return TestClient(app, raise_server_exceptions=False)


def _install(monkeypatch, manager: FakeManager) -> None:
    monkeypatch.setattr(link_routes, "_manager", lambda: manager)


def test_status_shape(monkeypatch, client):
    _install(monkeypatch, FakeManager(paired=True))
    reply = client.get("/api/kourichat-link/status")
    assert reply.status_code == 200
    body = reply.json()
    assert body["ok"] is True
    assert body["role"] == "secondary"
    assert body["paired"] is True
    assert body["master_endpoint"] == "192.168.1.20:27890"
    assert body["serving"] is False


def test_sync_error_maps_to_code_envelope(monkeypatch, client):
    _install(monkeypatch, FakeManager(error=SyncError(SyncErrorCode.UNREACHABLE, "host down")))
    for path in ("/api/kourichat-link/now", "/api/kourichat-link/serve"):
        reply = client.post(path, json={"start": True} if "serve" in path else None)
        assert reply.status_code == 400
        assert reply.json()["detail"] == {"code": "HM-6001", "message": "host down"}


def test_create_chain_conflict_is_409(monkeypatch, client):
    _install(monkeypatch, FakeManager())
    reply = client.post("/api/kourichat-link/create-chain")
    assert reply.status_code == 409
    detail = reply.json()["detail"]
    assert detail["code"] == "ALREADY_PAIRED"
    assert "already paired" in detail["message"]


def test_pair_rejects_a_wrong_word_count(monkeypatch, client):
    _install(monkeypatch, FakeManager())
    reply = client.post("/api/kourichat-link/pair", json={"endpoint": "h:1", "words": ["abandon"]})
    assert reply.status_code == 422  # the contract, not the domain


def test_pair_without_endpoint_reports_the_adopted_master(monkeypatch, client):
    """v2.8 无地址配对:an absent/blank endpoint means LAN discovery, and the reply carries
    the master that was actually adopted (settings), not the one that was asked for."""
    manager = FakeManager(paired=True)
    seen: list[str | None] = []

    def join_chain(words, endpoint):
        seen.append(endpoint)
        return SimpleNamespace(device_id="d" * 32, pinned_fingerprint="f" * 64)

    manager.join_chain = join_chain
    _install(monkeypatch, manager)

    words = ["abandon"] * 23 + ["art"]
    for body in ({"words": words}, {"endpoint": "   ", "words": words}):
        reply = client.post("/api/kourichat-link/pair", json=body)
        assert reply.status_code == 200
        assert reply.json()["master_endpoint"] == "192.168.1.20:27890"
    assert seen == [None, None]


def test_settings_defaults_roundtrip():
    # The section defaults the router surfaces must stay aligned with the shipped
    # constant module (config_defaults.py is ratchet-locked and holds no sync section).
    from hermes_cli.config_defaults_kourichat import KOURICHAT_LINK_DEFAULTS

    section = KOURICHAT_LINK_DEFAULTS
    assert SyncSettings(
        role=section["role"],
        master_endpoint=section["master_endpoint"],
        listen_port=section["listen_port"],
        auto_sync_minutes=section["auto_sync_minutes"],
    ) == SyncSettings()
    assert replace(SyncSettings(), role="master").role == "master"


def test_put_settings_persists(monkeypatch, client):
    _install(monkeypatch, FakeManager())
    reply = client.put("/api/kourichat-link/settings", json={"listen_port": 28000, "auto_sync_minutes": 30})
    assert reply.status_code == 200
    body = reply.json()
    assert body == {"ok": True, "listen_port": 28000, "auto_sync_minutes": 30}
    # The write went through the config seam, not just the response.
    from hermes_cli.sync_cmd import ConfigSettingsStore

    stored = ConfigSettingsStore().load()
    assert stored.listen_port == 28000 and stored.auto_sync_minutes == 30


def test_put_settings_validates(monkeypatch, client):
    _install(monkeypatch, FakeManager())
    assert client.put("/api/kourichat-link/settings", json={"listen_port": 0}).status_code == 422
    assert client.put("/api/kourichat-link/settings", json={"auto_sync_minutes": -5}).status_code == 422
    # A null field leaves the stored value untouched.
    reply = client.put("/api/kourichat-link/settings", json={"listen_port": None, "auto_sync_minutes": 60})
    assert reply.status_code == 200
    from hermes_cli.sync_cmd import ConfigSettingsStore

    stored = ConfigSettingsStore().load()
    assert stored.listen_port == 27890 and stored.auto_sync_minutes == 60


def test_exit_sync_is_swallowed_and_refreshes(tmp_path):
    """The exit path must never raise, and a master's final sync folds local edits."""
    from sync.manager import SyncManager, SyncSettings
    from tests.sync.conftest import MemoryMnemonicStore, MemorySettingsStore

    manager = SyncManager(tmp_path / "home", MemorySettingsStore(SyncSettings(role="disabled")), MemoryMnemonicStore())
    assert manager.sync_on_exit() is None  # unpaired: no-op, no raise

    (tmp_path / "master" / "notes").mkdir(parents=True)
    (tmp_path / "master" / "notes" / "a.md").write_text("late edit", encoding="utf-8")
    words_store = MemoryMnemonicStore()
    words_store.save(["abandon"] * 23 + ["art"])
    master = SyncManager(tmp_path / "master", MemorySettingsStore(SyncSettings(role="master")), words_store)
    from sync.state_store import SyncStateStore

    SyncStateStore(tmp_path / "master" / ".meta").create()
    outcome = master.sync_on_exit()
    assert outcome is not None and outcome.role == "master"
    assert master._state_store.load().entries[0].relative_path == "notes/a.md"
