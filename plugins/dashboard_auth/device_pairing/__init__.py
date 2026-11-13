"""DevicePairingProvider — one-time pairing code → long-lived, revocable device token.

Flow (PRD 远 04 / TODO T-09a): the operator runs ``hermes device-pair generate --label
<device>`` and types the printed 8-char code into the client, which POSTs
``/auth/password-login`` with ``{provider: "device-pairing", username: <label>,
password: <code>}``. The provider mints a 256-bit bearer token (shown once; only a salted
SHA-256 hash is stored) and returns it as the session's access token. From then on the client
presents ``Authorization: Bearer`` on REST (the gate's bearer leg) or ``?token=`` on the
``/api/ws`` upgrade — both verify through the SAME ``verify_session`` seam the interactive
providers use, so this plugin needs no core changes. ``hermes device-pair revoke <label|id>``
deletes the token; the next verify fails closed (401 everywhere).

Code semantics mirror ``gateway/pairing.py`` (8-char unambiguous alphabet, 1-hour expiry, one
code request per label per 10 min, lockout after 5 failed redemptions, 0600 data files, codes
and tokens never logged). The failure lockout is GLOBAL, not per label: labels are
attacker-chosen, so a per-label counter would be bypassable by rotation.

Deliberately NOT ``supports_token``: the token-auth seam (``register_token_route``) is for
service routes such as drain control, and a device token must never reach those. Device tokens
authenticate through the session-provider seam only.

Off by default: ``dashboard.device_pairing.enabled: true`` in config.yaml opts in. Storage
resolves fresh per call via ``get_hermes_dir``, so a multiplex process and the ``device-pair``
CLI agree on the active profile's directory the same way the pairing store does.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import logging
import os
import secrets
import sys
import threading
import time
from typing import Optional

from hermes_cli.dashboard_auth import (
    DashboardAuthProvider, InvalidCredentialsError, RefreshExpiredError, Session)
from plugins.dashboard_auth._shared import (
    NonInteractiveMixin, SkipRegistration, load_config_section, register_provider)

logger = logging.getLogger(__name__)
_TAG = "dashboard-auth-device-pairing"

LAST_SKIP_REASON: str = ""

# Pairing-code semantics mirror gateway/pairing.py (PRD 远 04: 码语义对齐).
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # unambiguous: no 0/O, 1/I
CODE_LENGTH = 8
CODE_TTL_SECONDS = 3600            # codes expire after 1 hour
RATE_LIMIT_SECONDS = 600           # one code request per label per 10 minutes
LOCKOUT_SECONDS = 3600             # lockout duration after too many failed redemptions
MAX_PENDING_CODES = 3              # outstanding codes at any time
MAX_FAILED_ATTEMPTS = 5            # failed redemptions before lockout

TOKEN_TTL_SECONDS = 365 * 24 * 60 * 60
_MAX_LABEL_CHARS = 64


def _hash_code(code: str, salt: bytes) -> str:
    """Salted SHA-256 — the only form a code or token ever takes on disk."""
    return hashlib.sha256(salt + code.encode("utf-8")).hexdigest()


def _is_hashed_entry(entry) -> bool:
    return isinstance(entry, dict) and isinstance(entry.get("salt"), str) \
        and isinstance(entry.get("hash"), str)


def _normalize_label(raw: str) -> str:
    label = " ".join(str(raw or "").split())[:_MAX_LABEL_CHARS]
    return label


def _load_json(path) -> dict:
    """Read a JSON object; ``{}`` when missing/malformed — a broken store verifies NOTHING."""
    import json

    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_json(path, data: dict) -> None:
    from utils import atomic_json_write

    atomic_json_write(path, data, mode=0o600)


_dir_lock_holders: dict = {}
_dir_lock_holders_guard = threading.Lock()


def _dir_lock_holder(directory) -> threading.local:
    with _dir_lock_holders_guard:
        return _dir_lock_holders.setdefault(str(directory), threading.local())


class DevicePairingStore:
    """Pending pairing codes and issued device tokens under ``<hermes_home>/dashboard/device-pairing``.

    Files: ``pending.json``, ``tokens.json``, shared ``_rate_limits.json`` — each 0600, written
    atomically, holding only salted hashes. The directory resolves fresh on every call so the
    dashboard process and the ``device-pair`` CLI never disagree about the active profile.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()  # adapters / request threads share one process

    def _dir(self):
        from hermes_constants import get_hermes_dir

        directory = get_hermes_dir("dashboard/device-pairing", "device-pairing")
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def _pending_path(self):
        return self._dir() / "pending.json"

    def _tokens_path(self):
        return self._dir() / "tokens.json"

    def _limits_path(self):
        return self._dir() / "_rate_limits.json"

    @contextlib.contextmanager
    def _locked(self):
        """Serialize load→mutate→save across threads AND processes (CLI + dashboard)."""
        from hermes_cli.auth import _file_lock

        directory = self._dir()
        with _file_lock(
            directory / ".lock", _dir_lock_holder(directory), 10.0,
            f"Timed out waiting for the device-pairing store lock ({directory})",
        ), self._lock:
            yield

    # ----- Pending codes -----

    def _cleanup_expired(self) -> None:
        path = self._pending_path()
        pending = _load_json(path)
        now = time.time()
        live = {
            entry_id: entry for entry_id, entry in pending.items()
            if isinstance(entry, dict) and isinstance(entry.get("created_at"), (int, float))
            and (now - entry["created_at"]) <= CODE_TTL_SECONDS
        }
        if len(live) != len(pending):
            _save_json(path, live)

    def generate_code(self, device_label: str) -> Optional[str]:
        """Mint a one-time code for ``device_label``; ``None`` when throttled/locked/full."""
        label = _normalize_label(device_label)
        if not label:
            return None
        with self._locked():
            self._cleanup_expired()
            if self._is_locked_out() or self._is_rate_limited(label):
                return None
            pending = _load_json(self._pending_path())
            if len(pending) >= MAX_PENDING_CODES:
                return None
            code = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))
            salt = os.urandom(16)
            pending[secrets.token_hex(8)] = {
                "hash": _hash_code(code, salt), "salt": salt.hex(),
                "device_label": label, "created_at": time.time(),
            }
            _save_json(self._pending_path(), pending)
            self._record_rate_limit(label)
            return code

    def redeem_code(self, device_label: str, code: str) -> Optional[str]:
        """Consume a valid code for ``device_label`` and mint a device token (returned once).

        ``None`` on an unknown/expired code, a label mismatch, or lockout — deliberately
        indistinguishable so redemption failures leak no store state. Failed attempts feed the
        global lockout regardless of which label was presented."""
        label = _normalize_label(device_label)
        code = "".join(str(code or "").upper().split())
        with self._locked():
            self._cleanup_expired()
            if self._is_locked_out():
                return None
            pending = _load_json(self._pending_path())
            for entry_id, entry in pending.items():
                if not _is_hashed_entry(entry) or entry.get("device_label") != label:
                    continue
                try:
                    salt = bytes.fromhex(entry["salt"])
                except ValueError:
                    continue
                if secrets.compare_digest(_hash_code(code, salt), entry["hash"]):
                    del pending[entry_id]
                    _save_json(self._pending_path(), pending)
                    self._reset_failed_attempts()
                    return self._mint_token(label)
        self._record_failed_attempt()
        return None

    # ----- Issued tokens -----

    def _mint_token(self, label: str) -> str:
        token = secrets.token_urlsafe(32)
        tokens = _load_json(self._tokens_path())
        entry_id = secrets.token_hex(8)
        salt = os.urandom(16)
        tokens[entry_id] = {
            "id": entry_id, "label": label, "hash": _hash_code(token, salt), "salt": salt.hex(),
            "created_at": time.time(), "expires_at": time.time() + TOKEN_TTL_SECONDS,
        }
        _save_json(self._tokens_path(), tokens)
        return token

    def verify(self, token: str) -> Optional[dict]:
        """``{id, label, expires_at}`` for a live (unexpired, unrevoked) token, else ``None``."""
        token = str(token or "").strip()
        if not token:
            return None
        now = time.time()
        for entry in _load_json(self._tokens_path()).values():
            if not _is_hashed_entry(entry):
                continue
            try:
                salt = bytes.fromhex(entry["salt"])
            except ValueError:
                continue
            if secrets.compare_digest(_hash_code(token, salt), entry["hash"]):
                if float(entry.get("expires_at") or 0) <= now:
                    return None
                return {"id": str(entry.get("id", "")), "label": str(entry.get("label", "")),
                        "expires_at": float(entry.get("expires_at") or 0)}
        return None

    def list_devices(self) -> list[dict]:
        """Issued tokens oldest-first; hashes never leave the store."""
        devices = [
            {"id": str(entry.get("id", "")), "label": str(entry.get("label", "")),
             "created_at": float(entry.get("created_at") or 0),
             "expires_at": float(entry.get("expires_at") or 0)}
            for entry in _load_json(self._tokens_path()).values() if _is_hashed_entry(entry)
        ]
        return sorted(devices, key=lambda device: device["created_at"])

    def revoke(self, device: str) -> bool:
        """Revoke by label (all its tokens) or by token id. True when something was removed."""
        needle = str(device or "").strip()
        if not needle:
            return False
        with self._locked():
            path = self._tokens_path()
            tokens = _load_json(path)
            remaining = {
                entry_id: entry for entry_id, entry in tokens.items()
                if not (entry.get("label") == needle or entry.get("id") == needle)
            }
            if len(remaining) == len(tokens):
                return False
            _save_json(path, remaining)
            return True

    # ----- Rate limiting / lockout (shared _rate_limits.json, gateway/pairing.py shape) -----

    def _limits(self) -> dict:
        return _load_json(self._limits_path())

    def _is_locked_out(self) -> bool:
        return time.time() < float(self._limits().get("_lockout:device", 0))

    def locked_out(self) -> bool:
        return self._is_locked_out()

    def code_rate_limited(self, device_label: str) -> bool:
        return self._is_rate_limited(_normalize_label(device_label))

    def _is_rate_limited(self, label: str) -> bool:
        return (time.time() - float(self._limits().get(f"code:{label}", 0))) < RATE_LIMIT_SECONDS

    def _record_rate_limit(self, label: str) -> None:
        limits = self._limits()
        limits[f"code:{label}"] = time.time()
        _save_json(self._limits_path(), limits)

    def _record_failed_attempt(self) -> None:
        limits = self._limits()
        failures = int(limits.get("_failures:device", 0)) + 1
        limits["_failures:device"] = failures
        if failures >= MAX_FAILED_ATTEMPTS:
            limits["_lockout:device"] = time.time() + LOCKOUT_SECONDS
            limits["_failures:device"] = 0
            logger.warning("device-pairing: locked out for %ss after %s failed redemptions",
                           LOCKOUT_SECONDS, MAX_FAILED_ATTEMPTS)
        _save_json(self._limits_path(), limits)

    def _reset_failed_attempts(self) -> None:
        limits = self._limits()
        if limits.get("_failures:device"):
            limits["_failures:device"] = 0
            _save_json(self._limits_path(), limits)


class DevicePairingProvider(NonInteractiveMixin, DashboardAuthProvider):
    """One-time-code → device-token provider (``supports_password`` + ``supports_session``)."""

    name = "device-pairing"
    display_name = "Device Pairing (one-time code → device token)"
    supports_password = True
    supports_session = True
    # The token-auth seam is for service routes (drain control…); a device token must never
    # reach those. Device tokens authenticate via the session-provider seam only.
    supports_token = False
    _NOT_INTERACTIVE = (
        "DevicePairingProvider has no OAuth flow; pair with `hermes device-pair generate`.")

    def __init__(self, *, store: DevicePairingStore) -> None:
        self._store = store

    # ---- password leg: the one-time code redeems here ----

    def complete_password_login(self, *, username: str, password: str) -> Session:
        token = self._store.redeem_code(username, password)
        if token is None:
            raise InvalidCredentialsError("invalid or expired pairing code")
        return self._session(token, self._store.verify(token))

    # ---- session leg: every gated REST bearer + /api/ws?token= verifies here ----

    def verify_session(self, *, access_token: str) -> Optional[Session]:
        entry = self._store.verify(access_token)
        if entry is None:
            return None
        return self._session(access_token, entry)

    def _session(self, token: str, entry: dict) -> Session:
        return Session(
            user_id=entry["label"], email="", display_name=entry["label"], org_id="",
            provider=self.name, expires_at=int(entry["expires_at"]), access_token=token,
            refresh_token="")

    def refresh_session(self, *, refresh_token: str) -> Session:
        raise RefreshExpiredError("device tokens do not refresh; pair the device again")

    def revoke_session(self, *, refresh_token: str) -> None:
        return None  # best-effort logout hook; revocation is `hermes device-pair revoke`


# ---- Plugin entry point ----

def _settings() -> dict:
    section = load_config_section(logger, _TAG, "dashboard", "device_pairing")
    if not section.get("enabled"):
        raise SkipRegistration(
            "dashboard.device_pairing.enabled is not true; the device-pairing provider stays "
            "off. Set it in config.yaml to let mobile clients pair via a one-time code "
            "(PRD 远 04) — it only matters once a non-loopback bind engages the auth gate.")
    return {"store": DevicePairingStore()}


def register(ctx) -> None:
    """Register the provider when config opts in; otherwise record the skip reason."""
    global LAST_SKIP_REASON
    LAST_SKIP_REASON = ""
    kwargs, LAST_SKIP_REASON = register_provider(ctx, logger, _TAG, DevicePairingProvider, _settings)
    if kwargs is None:
        return
    _register_cli(ctx)
    logger.info("dashboard-auth-device-pairing: registered device-token provider "
                "(pair via `hermes device-pair generate --label <device>`, revoke via "
                "`hermes device-pair revoke <label>`, list via `hermes device-pair list`)")


# ---- `hermes device-pair` CLI (registered only while the provider is enabled) ----

def _cli_setup(parser: argparse.ArgumentParser) -> None:
    subs = parser.add_subparsers(dest="device_pair_command", required=True)
    p_generate = subs.add_parser(
        "generate", help="Print a one-time pairing code for a device (redeem it within 1 hour)")
    p_generate.add_argument("--label", required=True, help="Device label shown in `device-pair list`")
    subs.add_parser("list", help="List paired devices")
    p_revoke = subs.add_parser("revoke", help="Revoke a device by label (or token id)")
    p_revoke.add_argument("device", help="Device label or token id")
    parser.set_defaults(func=_cli_dispatch)


def _cli_dispatch(args: argparse.Namespace) -> int:
    store = DevicePairingStore()
    command = args.device_pair_command
    if command == "generate":
        return _cli_generate(store, args.label)
    if command == "list":
        return _cli_list(store)
    if command == "revoke":
        return 0 if store.revoke(args.device) else _revoke_miss(store, args.device)
    print(f"unknown subcommand: {command}", file=sys.stderr)
    return 2


def _cli_generate(store: DevicePairingStore, label: str) -> int:
    code = store.generate_code(label)
    if code is None:
        if store.locked_out():
            print("Pairing is locked out after repeated failed redemptions; wait an hour and retry.",
                  file=sys.stderr)
        else:
            print("No code issued (per-device 10-minute throttle, or too many pending codes). "
                  "Retry later or `device-pair revoke` an old device.", file=sys.stderr)
        return 1
    print(f"Pairing code for {label} (valid 1 hour, shown once):")
    print(f"  {code}")
    print("On the device, authenticate with:")
    print('  POST /auth/password-login {"provider": "device-pairing", '
          f'"username": "{label}", "password": "{code}"}}')
    print("Then present the returned token as `Authorization: Bearer` (REST) or "
          "`/api/ws?token=` (WebSocket).")
    return 0


def _cli_list(store: DevicePairingStore) -> int:
    devices = store.list_devices()
    if not devices:
        print("No paired devices.")
        return 0
    print(f"{len(devices)} paired device(s):")
    for device in devices:
        print(f"  {device['label']}  id={device['id']}  "
              f"expires={time.strftime('%Y-%m-%d', time.localtime(device['expires_at']))}")
    return 0


def _revoke_miss(store: DevicePairingStore, device: str) -> int:
    print(f"No paired device matches {device!r}. Run `hermes device-pair list`.", file=sys.stderr)
    return 1


def _register_cli(ctx) -> None:
    ctx.register_cli_command(
        name="device-pair", help="Pair mobile devices via a one-time code (device tokens)",
        setup_fn=_cli_setup, handler_fn=_cli_dispatch)
