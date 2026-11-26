"""Profile-owned Weixin QR pairing. Only the scannable URL leaves the backend."""

import asyncio
import logging
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, HTTPException

from gateway.platform_registry import platform_registry
from hermes_cli.config import require_env_writable, save_env_value
from hermes_cli.plugins import discover_plugins
from hermes_cli.web_server_gateway import _restart_gateway_after
from hermes_cli.web_server_messaging import _write_platform_enabled
from hermes_cli.web_server_profiles import _config_profile_scope
from hermes_constants import get_hermes_home

router = APIRouter(prefix="/api/messaging/weixin/onboarding")
_log = logging.getLogger(__name__)


@dataclass
class Pairing:
    id: str
    status: str = "waiting"
    qr_payload: str = ""
    needs_restart: bool = False
    task: asyncio.Task | None = None

    def public(self) -> dict[str, str | bool]:
        return {
            "pairing_id": self.id,
            "status": self.status,
            "qr_payload": self.qr_payload,
            "needs_restart": self.needs_restart,
        }


_pairings: dict[Path, Pairing] = {}


async def _pair(
    home: Path,
    profile: str | None,
    pairing: Pairing,
    login: Callable[[str, Callable[[str], None]], Awaitable[dict[str, str]]],
) -> None:
    from hermes_cli.web_routers.messaging import _notify_multiplexer_hot_serve

    def save(credentials: dict[str, str]) -> None:
        for key in credentials:
            require_env_writable(key, "set")
        for key, value in credentials.items():
            save_env_value(key, value)
        _write_platform_enabled("weixin", True)

    def show_qr(url: str) -> None:
        pairing.qr_payload = url

    try:
        with _config_profile_scope(profile):
            credentials = await asyncio.wait_for(login(str(home), show_qr), timeout=480)
            pairing.status = "saving"
            await asyncio.to_thread(save, credentials)
            pairing.needs_restart = True
            if await asyncio.to_thread(_notify_multiplexer_hot_serve, profile):
                pairing.needs_restart = False
            else:
                restart = await asyncio.to_thread(
                    _restart_gateway_after, profile, what="Weixin onboarding", label="Weixin"
                )
                pairing.needs_restart = not restart["restart_started"]
            pairing.status = "connected"
    except Exception as exc:  # health: allow BLE001 -- Plugin and network exceptions may contain QR tokens or credentials.
        _log.warning("Weixin pairing failed (%s)", type(exc).__name__)
        pairing.status = "connected" if pairing.needs_restart else "error"
    finally:
        pairing.qr_payload = ""

        def forget() -> None:
            if _pairings.get(home) is pairing:
                del _pairings[home]

        asyncio.get_running_loop().call_later(60, forget)


@router.post("/start")
async def start(profile: str | None = None):
    with _config_profile_scope(profile):
        home = get_hermes_home()
        await asyncio.to_thread(discover_plugins)
        entry = platform_registry.get("weixin")
        if not entry or not entry.dashboard_qr_login:
            raise HTTPException(409, "Weixin QR onboarding is unavailable for this profile.")
        # Concurrent starts can interleave during discovery.
        current = _pairings.get(home)
        if current and current.status in {"waiting", "saving"}:
            return current.public()
        pairing = _pairings[home] = Pairing(secrets.token_urlsafe(24))
        pairing.task = asyncio.create_task(_pair(home, profile, pairing, entry.dashboard_qr_login))
        return pairing.public()


def _owned(pairing_id: str, profile: str | None) -> Pairing:
    with _config_profile_scope(profile):
        pairing = _pairings.get(get_hermes_home())
    if not pairing or pairing.id != pairing_id:
        raise HTTPException(404, "Weixin pairing not found.")
    return pairing


@router.get("/{pairing_id}")
async def status(pairing_id: str, profile: str | None = None):
    return _owned(pairing_id, profile).public()


@router.delete("/{pairing_id}")
async def cancel(pairing_id: str, profile: str | None = None):
    pairing = _owned(pairing_id, profile)
    if pairing.status == "waiting" and pairing.task:
        pairing.task.cancel()
        pairing.status = "cancelled"
        pairing.qr_payload = ""
    return pairing.public()
