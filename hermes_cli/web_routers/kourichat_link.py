"""KouriChat Link dashboard routes: device-chain sync status, pairing and sync ops.

The desktop Capabilities page (and any dashboard client) drives the same
:mod:`sync.manager` the ``hermes sync`` CLI uses. Every route takes the optional
``profile`` query param and resolves the manager inside that profile's home + secret
scope, never the launch profile by default; the destructive routes additionally demand
an explicit profile on a multiplexing backend (``destructive_profile``).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from hermes_cli.web_routers._common import (
    config_scoped_to_thread,
    destructive_profile,
    http_failure,
)

_log = logging.getLogger("hermes_cli.web_server")
router = APIRouter()

_ROUTE = "kourichat-link"


def _manager():
    from hermes_cli.sync_cmd import shared_manager

    return shared_manager()


def _sync_http(exc) -> HTTPException:
    """An HM-600x domain failure as a 4xx detail the client can branch on."""
    return HTTPException(status_code=400, detail={"code": exc.code.value, "message": exc.detail or exc.code.name.lower()})


def _already_paired(exc: ValueError) -> HTTPException:
    return HTTPException(status_code=409, detail={"code": "ALREADY_PAIRED", "message": str(exc)})


@router.get("/api/kourichat-link/status")
async def link_status(profile: Optional[str] = None) -> dict:
    def read():
        manager = _manager()
        status = manager.status()
        return {
            "ok": True,
            "role": status.role,
            "paired": status.paired,
            "device_id": status.device_id,
            "master_endpoint": status.master_endpoint,
            "listen_port": status.listen_port,
            "auto_sync_minutes": status.auto_sync_minutes,
            "pinned_fingerprint": status.pinned_fingerprint,
            "last_sync_at_ms": status.last_sync_at_ms,
            "last_sync_error": status.last_sync_error,
            "applied_count": status.applied_count,
            "archived_count": status.archived_count,
            "tracked_files": status.tracked_files,
            "syncable_files": status.syncable_files,
            "serving": manager.serving,
        }

    return await config_scoped_to_thread(profile, read)


class LinkPairBody(BaseModel):
    endpoint: Optional[str] = None  # empty/absent: auto-discover the master on this LAN
    words: list[str] = Field(min_length=24, max_length=24)


@router.post("/api/kourichat-link/pair")
async def link_pair(body: LinkPairBody, profile: Optional[str] = None) -> dict:
    def pair():
        manager = _manager()
        try:
            state = manager.join_chain(body.words, (body.endpoint or "").strip() or None)
        except ValueError as exc:
            raise _already_paired(exc) from exc
        return {
            "ok": True,
            "device_id": state.device_id,
            "pinned_fingerprint": state.pinned_fingerprint,
            "master_endpoint": manager.status().master_endpoint,
        }

    return await _link_op(profile, "pair", pair)


@router.post("/api/kourichat-link/create-chain")
async def link_create_chain(profile: Optional[str] = None) -> dict:
    def create():
        try:
            words = _manager().create_chain()
        except ValueError as exc:
            raise _already_paired(exc) from exc
        # The words are shown exactly once; the client tells the user to write them down.
        return {"ok": True, "words": words}

    return await _link_op(profile, "create-chain", create)


@router.post("/api/kourichat-link/now")
async def link_now(profile: Optional[str] = None) -> dict:
    def now():
        outcome = _manager().sync_now()
        return {"ok": True, "role": outcome.role, "records": outcome.records, "applied": outcome.applied}

    return await _link_op(profile, "now", now)


class LinkSettingsBody(BaseModel):
    listen_port: Optional[int] = Field(default=None, ge=1, le=65535)
    auto_sync_minutes: Optional[int] = Field(default=None, ge=0)


@router.put("/api/kourichat-link/settings")
async def link_update_settings(body: LinkSettingsBody, profile: Optional[str] = None) -> dict:
    def update():
        from hermes_cli.sync_cmd import update_settings

        settings = update_settings(
            listen_port=body.listen_port,
            auto_sync_minutes=body.auto_sync_minutes,
        )
        return {"ok": True, "listen_port": settings.listen_port, "auto_sync_minutes": settings.auto_sync_minutes}

    return await _link_op(profile, "settings", update)


class LinkServeBody(BaseModel):
    start: bool


@router.post("/api/kourichat-link/serve")
async def link_serve(body: LinkServeBody, profile: Optional[str] = None) -> dict:
    def serve():
        manager = _manager()
        if body.start:
            server = manager.start_server()
            return {"ok": True, "serving": True, "port": server.bound_port}
        manager.stop_server()
        return {"ok": True, "serving": False, "port": None}

    return await _link_op(profile, "serve", serve)


@router.post("/api/kourichat-link/disconnect")
async def link_disconnect(request: Request, profile: Optional[str] = None) -> dict:
    destructive_profile(profile, f"{request.url.path}")
    return await _link_op(profile, "disconnect", lambda: (_manager().disconnect_and_clear(), {"ok": True})[1])


@router.post("/api/kourichat-link/reset-chain")
async def link_reset_chain(request: Request, profile: Optional[str] = None) -> dict:
    destructive_profile(profile, f"{request.url.path}")
    return await _link_op(profile, "reset-chain", lambda: (_manager().reset_chain(), {"ok": True})[1])


async def _link_op(profile: Optional[str], name: str, op) -> dict:
    """One sync operation under the profile's home + secret scope; HM-600x becomes a
    400 detail, an unexpected failure a logged 500 (``http_failure``)."""

    def run():
        from sync.errors import SyncError

        try:
            return op()
        except SyncError as exc:
            raise _sync_http(exc) from exc

    with http_failure(f"kourichat-link {name} failed", 500, f"KouriChat Link {name}"):
        return await config_scoped_to_thread(profile, run)
