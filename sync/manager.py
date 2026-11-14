"""Role lifecycle for KouriChat Link on the PC: create/join a chain, sync, reset.

Mirrors the Android ``SyncManager.kt`` responsibilities with hermes seams injected:
settings (config.yaml) and the mnemonic (.env) live behind small stores the CLI/desktop
provide, so this package stays independent of hermes configuration internals.

Pairing is all-or-nothing: ``join_chain`` pulls and applies before persisting anything,
and a failure anywhere leaves no half pairing behind. The mnemonic is the sole root
credential — resetting the chain (master) or disconnecting (secondary) discards pairing
but never deletes synced files.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Protocol

from sync import scope
from sync.client import SyncClient
from sync.discovery import LinkDiscovery
from sync.engine import SyncEngine
from sync.errors import SyncErrorCode, SyncError
from sync.identity import FileSyncIdentity
from sync.keys import SyncKeys
from sync.mnemonic import SyncMnemonic
from sync.protocol import DEFAULT_PORT
from sync.records import SyncEnvelope
from sync.server import SyncRecordSource, SyncServer
from sync.state_store import SyncState, SyncStateStore

ROLE_DISABLED = "disabled"
ROLE_MASTER = "master"
ROLE_SECONDARY = "secondary"

_META_DIR = ".meta"
_IDENTITY_DIR = "identity"

#: LAN-scan budget when pairing without an address (Android ``JOIN_DISCOVERY_TIMEOUT_MS``).
_JOIN_DISCOVERY_TIMEOUT_MS = 8_000


@dataclass(frozen=True, slots=True)
class SyncSettings:
    role: str = ROLE_DISABLED
    master_endpoint: str = ""
    listen_port: int = DEFAULT_PORT
    auto_sync_minutes: int = 0


class SyncSettingsStore(Protocol):
    def load(self) -> SyncSettings: ...

    def update(self, settings: SyncSettings) -> None: ...


class MnemonicStore(Protocol):
    def load(self) -> list[str] | None: ...

    def save(self, words: list[str]) -> None: ...

    def clear(self) -> None: ...


@dataclass(frozen=True, slots=True)
class SyncStatus:
    role: str
    paired: bool
    device_id: str | None = None
    master_endpoint: str = ""
    listen_port: int = DEFAULT_PORT
    auto_sync_minutes: int = 0
    pinned_fingerprint: str | None = None
    last_sync_at_ms: int | None = None
    last_sync_error: str | None = None
    applied_count: int = 0
    archived_count: int = 0
    tracked_files: int = 0
    syncable_files: int = 0


@dataclass(frozen=True, slots=True)
class SyncOutcome:
    role: str
    records: int
    applied: int


def parse_endpoint(endpoint: str) -> tuple[str, int]:
    text = endpoint.strip()
    if not text:
        raise SyncError(SyncErrorCode.NOT_PAIRED, "no master endpoint configured")
    host, sep, port_text = text.rpartition(":")
    if not sep or not host:
        raise SyncError(SyncErrorCode.VERIFY, f"endpoint must be host:port, got {endpoint!r}")
    try:
        port = int(port_text)
    except ValueError as error:
        raise SyncError(SyncErrorCode.VERIFY, f"endpoint port is not a number: {endpoint!r}") from error
    return host, port


class SyncManager:
    def __init__(
        self,
        root: Path,
        settings_store: SyncSettingsStore,
        mnemonic_store: MnemonicStore,
        mnemonic: SyncMnemonic | None = None,
        pre_sync: Callable[[], None] | None = None,
        post_sync: Callable[[], None] | None = None,
    ) -> None:
        self._root = Path(root)
        self._settings_store = settings_store
        self._mnemonic_store = mnemonic_store
        self._mnemonic = mnemonic or SyncMnemonic()
        self._state_store = SyncStateStore(self._root / _META_DIR)
        self._server: SyncServer | None = None
        self._discovery = LinkDiscovery()
        # Kouri Harness-layer hooks (chat-transcript bridge): export before a cycle lands the
        # local log, import after records arrive. The sync package stays storage-agnostic.
        self._pre_sync = pre_sync
        self._post_sync = post_sync

    # ── status ──────────────────────────────────────────────────────────────────

    @property
    def root(self) -> Path:
        return self._root

    def status(self) -> SyncStatus:
        settings = self._settings_store.load()
        state = self._state_store.load() if self._state_store.exists() else None
        return SyncStatus(
            role=settings.role,
            paired=state is not None,
            device_id=state.device_id if state else None,
            master_endpoint=settings.master_endpoint,
            listen_port=settings.listen_port,
            auto_sync_minutes=settings.auto_sync_minutes,
            pinned_fingerprint=state.pinned_fingerprint if state else None,
            last_sync_at_ms=state.last_sync_at_ms if state else None,
            last_sync_error=state.last_sync_error if state else None,
            applied_count=state.applied_count if state else 0,
            archived_count=state.archived_count if state else 0,
            tracked_files=len(state.entries) if state else 0,
            syncable_files=len(scope.scan(self._root)),
        )

    # ── pairing ─────────────────────────────────────────────────────────────────

    def create_chain(self) -> list[str]:
        """Become the master: generate the 24 words (the caller displays them for paper
        backup), persist the credential and start the chain as master role."""
        self._require_unpaired()
        words = self._mnemonic.generate()
        self._mnemonic_store.save(words)
        if self._state_store.create() is None:
            self._mnemonic_store.clear()
            raise SyncError(SyncErrorCode.STATE_CORRUPT, "cannot write sync state")
        self._settings_store.update(replace(self._settings_store.load(), role=ROLE_MASTER))
        # A master that never starts serving is undiscoverable, which is the most common
        # "the phone can't find the PC" report: start it as part of becoming the master.
        # best-effort — a bind failure is surfaced later by start_server/status.
        try:
            self.start_server()
        except Exception as error:  # health: allow BLE001 -- becoming the master must not fail on a port/interface problem; the UI reports serving=false and can retry
            logging.getLogger(__name__).debug("master auto-serve failed: %s", error)
        return words

    def join_chain(self, words: list[str], endpoint: str | None = None) -> SyncState:
        """Join as a secondary (Android 同 01/11, v2.8): validate the words, then
        negotiate — with the given master when an endpoint is supplied (QR / manual),
        falling back to LAN discovery on an unreachable answer, or by discovering
        candidates when no endpoint is given at all (candidates may include masters of
        OTHER chains: only the one whose challenge-response passes is the same chain).
        Success persists the pairing; total failure leaves nothing paired."""
        self._require_unpaired()
        keys = SyncKeys.from_words(words, self._mnemonic)
        if keys is None:
            raise SyncError(SyncErrorCode.NOT_PAIRED, "invalid mnemonic")
        state = self._state_store.create()
        if state is None:
            raise SyncError(SyncErrorCode.STATE_CORRUPT, "cannot write sync state")
        engine = self._engine_with_words(words)
        self._run_hook("pre")

        candidates: list[tuple[str, int]]
        if endpoint:
            host, port = parse_endpoint(endpoint)
            candidates = [(host, port)]
        else:
            candidates = self._discovery.discover(_JOIN_DISCOVERY_TIMEOUT_MS)
            candidates = [(host, port) for host, port in candidates if 1 <= port <= 65535]
        if not candidates:
            self._state_store.clear()
            raise SyncError(SyncErrorCode.UNREACHABLE, "no master found on this LAN; start the master's sync server first")

        client = SyncClient()
        last_error: SyncError | None = None
        for host, port in candidates:
            result = self._join_at(client, engine, keys, words, host, port)
            if isinstance(result, SyncState):
                self._run_hook("post")
                return result
            last_error = result
            # Candidates may include masters of other chains: every failure moves on;
            # only a full negotiation pins the pairing (Android joinChain's loop).
        self._state_store.clear()
        self._record_sync_error(last_error or SyncError(SyncErrorCode.UNREACHABLE, "no master answered"))
        raise last_error or SyncError(SyncErrorCode.UNREACHABLE, "no master answered")

    def _join_at(
        self, client: SyncClient, engine: SyncEngine, keys: SyncKeys, words: list[str], host: str, port: int
    ) -> SyncState | SyncError:
        """One join attempt against (host, port): full negotiation, then persist. A
        failure returns the error so the candidate loop can try the next master
        (Android ``joinAt`` + ``joinChain``)."""
        state = self._state_store.load()
        if state is None:
            return SyncError(SyncErrorCode.STATE_CORRUPT, "cannot write sync state")
        apply_error: list[SyncErrorCode] = []

        def exchange(envelopes: list) -> list:
            error = engine.apply_remote(envelopes, keys)
            if error is not None:
                apply_error.append(error)
                return []
            # v2.8 two-way: notes written before joining push to the master too.
            return engine.compute_outgoing(envelopes, keys)

        try:
            result = client.sync(
                host,
                port,
                state.device_id,
                keys,
                pinned_fingerprint=None,
                exchange=exchange,
            )
        except SyncError as error:
            self._record_sync_error(error)
            return error
        if apply_error:
            failure = SyncError(apply_error[0], "initial pull rejected")
            self._record_sync_error(failure)
            return failure
        if self._state_store.update(lambda current: replace(current, pinned_fingerprint=result.fingerprint)) is None:
            return SyncError(SyncErrorCode.STATE_CORRUPT, "cannot pin master fingerprint")
        self._mnemonic_store.save(words)
        self._settings_store.update(replace(self._settings_store.load(), role=ROLE_SECONDARY, master_endpoint=f"{host}:{port}"))
        self._state_store.update(lambda current: replace(current, last_sync_at_ms=int(time.time() * 1000), last_sync_error=None))
        return self._state_store.load() or state

    # ── sync ────────────────────────────────────────────────────────────────────

    def sync_now(self) -> SyncOutcome:
        """Role-aware manual sync: the master refreshes its record log, the secondary
        runs one two-way negotiation. Raises :class:`SyncError` on failure."""
        settings = self._settings_store.load()
        if settings.role == ROLE_SECONDARY:
            return self._sync_once_secondary(settings)
        if settings.role == ROLE_MASTER:
            if self._state_store.load() is None:
                raise SyncError(SyncErrorCode.STATE_CORRUPT, "master state missing; run create-chain again")
            # Someone is asking for a sync: an idle master (fresh start, port rebound)
            # must be reachable again, not just refresh its own log.
            if not self.serving:
                try:
                    self.start_server()
                except Exception as error:  # health: allow BLE001 -- a bind failure is reported by the outcome below; the record refresh still has value
                    logging.getLogger(__name__).debug("master serve on sync failed: %s", error)
            self._run_hook("pre")
            if not self._engine().refresh_from_local_changes():
                raise SyncError(SyncErrorCode.STATE_CORRUPT, "record log refresh failed")
            self._run_hook("post")
            return SyncOutcome(ROLE_MASTER, len(scope.scan(self._root)), 0)
        raise SyncError(SyncErrorCode.NOT_PAIRED, "sync is not enabled")

    def sync_on_exit(self) -> SyncOutcome | None:
        """Best-effort final sync when the PC software is leaving: a secondary runs one
        last negotiation (falling back to LAN discovery when the master is unreachable);
        a master refreshes its record log so devices pulling next get everything. Any
        failure is swallowed — an exit path must not raise."""
        try:
            settings = self._settings_store.load()
            if settings.role == ROLE_SECONDARY:
                return self._sync_once_secondary(settings, allow_discovery=True)
            if settings.role == ROLE_MASTER:
                if self._state_store.load() is not None:
                    self._run_hook("pre")
                    self._engine().refresh_from_local_changes()
                    self._run_hook("post")
                return SyncOutcome(ROLE_MASTER, len(scope.scan(self._root)), 0)
        except Exception as error:  # health: allow BLE001 -- the exit contract: a leaving PC must never crash on a sync failure, and the caller surfaces it
            logging.getLogger(__name__).debug("exit sync skipped: %s", error)
            return None
        return None

    def _run_hook(self, which: str) -> None:
        hook = self._pre_sync if which == "pre" else self._post_sync
        if hook is not None:
            hook()

    def _sync_once_secondary(self, settings: SyncSettings, *, allow_discovery: bool = True) -> SyncOutcome:
        """One full two-way negotiation with the master (Android v2.8): apply the remote
        batch, then push this device's locally-newer records. The saved endpoint is tried
        first; an unreachable master falls back to LAN discovery (Android ``pull``'s
        mDNS retry), and a successful negotiation writes the working endpoint back."""
        state = self._state_store.load()
        if state is None:
            raise SyncError(
                SyncErrorCode.STATE_CORRUPT if self._state_store.exists() else SyncErrorCode.NOT_PAIRED,
                "no usable sync state",
            )
        words = self._mnemonic_store.load()
        if not words:
            raise SyncError(SyncErrorCode.NOT_PAIRED, "mnemonic missing; pair again")
        keys = SyncKeys.from_words(words, self._mnemonic)
        if keys is None:
            raise SyncError(SyncErrorCode.NOT_PAIRED, "stored mnemonic invalid; pair again")
        self._run_hook("pre")
        # The LAN moves (DHCP renewal, Wi-Fi roam, VPN up/down): the saved endpoint is a
        # hint, never the truth. Every cycle rediscovers first and only falls back to the
        # last-known address when the scan finds nothing; the winner is written back below.
        saved: tuple[str, int] | None = None
        if (settings.master_endpoint or "").strip():
            saved = parse_endpoint(settings.master_endpoint)
        candidates: list[tuple[str, int]] = []
        if allow_discovery:
            candidates.extend(self._discovery.discover(port=saved[1] if saved else DEFAULT_PORT))
        if saved is not None and saved not in candidates:
            candidates.append(saved)
        if not candidates:
            raise SyncError(SyncErrorCode.NOT_PAIRED, "no master endpoint configured and none discovered")
        client = SyncClient()
        engine = self._engine_with_words(words)
        last_error: SyncError | None = None
        for host, port in candidates:
            applied_before = state.applied_count
            pulled = {"records": 0}
            apply_error: list[SyncErrorCode] = []

            def exchange(envelopes: list) -> list:
                """The negotiation callback: apply the remote batch first — a failure
                aborts the whole cycle with nothing pushed (Android pullAt)."""
                error = engine.apply_remote(envelopes, keys)
                if error is not None:
                    apply_error.append(error)
                    return []
                pulled["records"] = len(envelopes)
                return engine.compute_outgoing(envelopes, keys)

            try:
                result = client.sync(
                    host,
                    port,
                    state.device_id,
                    keys,
                    pinned_fingerprint=state.pinned_fingerprint,
                    exchange=exchange,
                )
            except SyncError as error:
                last_error = error
                self._record_sync_error(error)
                if error.code is not SyncErrorCode.UNREACHABLE:
                    raise
                continue  # try the next discovered candidate
            if apply_error:
                failure = SyncError(apply_error[0], "remote batch rejected")
                self._record_sync_error(failure)
                raise failure
            # Authenticated + negotiated: pin the observed fingerprint (TOFU refresh) and
            # keep the working endpoint for next time (Android pullAt's writes).
            self._state_store.update(lambda current: replace(current, pinned_fingerprint=result.fingerprint))
            self._settings_store.update(replace(settings, master_endpoint=f"{host}:{port}"))
            self._run_hook("post")
            after = self._state_store.load()
            return SyncOutcome(
                ROLE_SECONDARY,
                pulled["records"],
                (after.applied_count if after else applied_before) - applied_before,
            )
        raise last_error or SyncError(SyncErrorCode.UNREACHABLE, "no master answered")

    def _record_sync_error(self, error: SyncError) -> None:
        # Best-effort bookkeeping: update() already absorbs state-store failures and the
        # transform cannot raise, so the original error below still propagates either way.
        self._state_store.update(lambda current: replace(current, last_sync_error=str(error)))

    # ── serving (master role) ───────────────────────────────────────────────────

    def start_server(self) -> SyncServer:
        if self._server is not None and self._server.running:
            return self._server
        identity = FileSyncIdentity(self._root / _META_DIR / _IDENTITY_DIR)
        server = SyncServer(self._settings_store.load().listen_port, identity, self._record_source())
        server.start()
        self._server = server
        # Advertise on the LAN so a phone (or another PC) can discover this master
        # without typing an address (Android 同 01); no-op without zeroconf.
        self._discovery.register(server.bound_port or self._settings_store.load().listen_port)
        return server

    def stop_server(self) -> None:
        self._discovery.unregister()
        if self._server is not None:
            self._server.stop()
            self._server = None

    @property
    def serving(self) -> bool:
        return self._server is not None and self._server.running

    def _record_source(self) -> SyncRecordSource:
        return _HookedSource(self._engine(), self._post_sync)

    # ── teardown ────────────────────────────────────────────────────────────────

    def reset_chain(self) -> None:
        """Master kills the whole chain: stop serving and discard pairing on this device."""
        self.stop_server()
        self._teardown()

    def disconnect_and_clear(self) -> None:
        """Secondary leaves the chain: pairing is discarded, synced files stay."""
        self._teardown()

    def _teardown(self) -> None:
        self._state_store.clear()
        self._mnemonic_store.clear()
        self._settings_store.update(replace(self._settings_store.load(), role=ROLE_DISABLED, master_endpoint=""))

    # ── internals ───────────────────────────────────────────────────────────────

    def _require_unpaired(self) -> None:
        if self._state_store.exists():
            raise ValueError("already paired; disconnect or reset first")

    def _engine(self) -> SyncEngine:
        return SyncEngine(self._root, self._state_store, self._mnemonic, self._mnemonic_store.load)

    def _engine_with_words(self, words: list[str]) -> SyncEngine:
        """An engine bound to just-entered words that are not persisted yet (join flow)."""
        return SyncEngine(self._root, self._state_store, self._mnemonic, lambda: words)


class _HookedSource:
    """The engine as the master's record source, with the hermes-layer post-sync hook
    (chat-transcript import) firing after pushed records are applied."""

    def __init__(self, engine: SyncEngine, post_sync: Callable[[], None] | None) -> None:
        self._engine = engine
        self._post_sync = post_sync

    def auth_key(self) -> bytes | None:
        return self._engine.auth_key()

    def list_records(self) -> list[SyncEnvelope]:
        return self._engine.list_records()

    def record(self, record_id: str) -> SyncEnvelope | None:
        return self._engine.record(record_id)

    def push(self, envelopes: list[SyncEnvelope]) -> SyncErrorCode | None:
        error = self._engine.push(envelopes)
        if error is None and self._post_sync is not None:
            self._post_sync()
        return error
