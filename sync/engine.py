"""The sync engine: master record-log maintenance and secondary remote apply.

Mirrors the Android ``SyncEngine.kt``. The master maintains the record log (scan the
whitelist, diff digests, tick the clock, tombstone removals) and re-encrypts current
content on demand; the secondary unpacks a whole batch first (any failure rejects the
batch, fail closed → HM-6003), then applies per path strictly-newer-wins by HLC, keeping
a ``.versions/`` archive before every overwrite or delete and never touching files it
does not know to be sync mirrors. Conflicts are not arbitrated: phase 1 is
master-exclusive-write.
"""

from __future__ import annotations

import shutil
import time
from dataclasses import replace
from pathlib import Path
from typing import Callable

from sync import scope
from sync.clock import SyncClock
from sync.crypto import sha256_hex
from sync.errors import SyncErrorCode
from sync.keys import SyncKeys
from sync.mnemonic import SyncMnemonic
from sync.records import SyncEnvelope, SyncPayload, pack, record_id, unpack
from sync.state_store import SyncEntry, SyncStateStore, SyncTombstone

_VERSIONS_DIR = ".versions"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _archive(root: Path, relative_path: str, file: Path) -> None:
    """Keep one copy of the current content under ``.versions/<path with _>.<stamp>``
    (Android ``MemoryRepository.backup``; the directory never syncs)."""
    versions_dir = root / _VERSIONS_DIR
    versions_dir.mkdir(parents=True, exist_ok=True)
    label = relative_path.replace("/", "_")
    stamp = _now_ms()
    target = versions_dir / f"{label}.{stamp}"
    while target.exists():
        stamp += 1
        target = versions_dir / f"{label}.{stamp}"
    shutil.copyfile(file, target)


def read_current(root: Path, relative_path: str) -> str | None:
    """Current file content as text (UTF-8), or ``None`` when absent/unreadable."""
    if not scope.is_syncable_path(relative_path):
        return None
    file = root / relative_path
    try:
        return file.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def apply_sync_file(root: Path, relative_path: str, content: str) -> bool:
    """Whole-file overwrite inside the whitelist, archiving existing content first."""
    if not scope.is_syncable_path(relative_path):
        return False
    target = (root / relative_path).resolve()
    root_resolved = Path(root).resolve()
    if root_resolved not in target.parents:
        return False
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file() and target.stat().st_size > 0:
            _archive(root, relative_path, target)
        target.write_bytes(content.encode("utf-8"))
        return True
    except OSError:
        return False


def archive_and_delete(root: Path, relative_path: str) -> bool:
    """Tombstone apply: archive first, delete only when the archive succeeded."""
    if not scope.is_syncable_path(relative_path):
        return False
    target = (root / relative_path).resolve()
    root_resolved = Path(root).resolve()
    if root_resolved not in target.parents:
        return False
    try:
        if target.is_file():
            if target.stat().st_size > 0:
                _archive(root, relative_path, target)
            target.unlink()
        return True
    except OSError:
        return False


class SyncEngine:
    def __init__(
        self,
        root: Path,
        state_store: SyncStateStore,
        mnemonic: SyncMnemonic,
        mnemonic_provider: Callable[[], list[str] | None],
    ) -> None:
        self._root = Path(root)
        self._state_store = state_store
        self._mnemonic = mnemonic
        self._mnemonic_provider = mnemonic_provider

    # ── SyncRecordSource (master side) ──────────────────────────────────────────

    def auth_key(self) -> bytes | None:
        keys = self._keys()
        return keys.auth_key() if keys else None

    def list_records(self) -> list[SyncEnvelope]:
        keys = self._keys()
        state = self._state_store.load()
        if keys is None or state is None:
            return []
        envelopes: list[SyncEnvelope] = []
        for entry in state.entries:
            content = read_current(self._root, entry.relative_path)
            if content is None:
                continue
            envelope = pack(keys, entry.hlc, state.device_id, SyncPayload(entry.relative_path, content))
            if envelope is not None:
                envelopes.append(envelope)
        for tombstone in state.tombstones:
            envelope = pack(keys, tombstone.hlc, state.device_id, SyncPayload(tombstone.relative_path, deleted=True))
            if envelope is not None:
                envelopes.append(envelope)
        return envelopes

    def record(self, record_id_hex: str) -> SyncEnvelope | None:
        return next((envelope for envelope in self.list_records() if envelope.record_id == record_id_hex), None)

    def push(self, envelopes: list[SyncEnvelope]) -> SyncErrorCode | None:
        """Master receives a secondary's locally-newer records: the same archive-then-
        overwrite and HLC merge as the secondary's own apply (Android v2.8)."""
        keys = self._keys()
        if keys is None:
            return SyncErrorCode.NOT_PAIRED
        return self.apply_remote(envelopes, keys)

    # ── record-log maintenance (master side) ────────────────────────────────────

    def refresh_from_local_changes(self, now_ms: int | None = None) -> bool:
        """Rescan the whitelist after local changes; diff out additions/edits/deletions
        and advance the HLC. Idempotent and cheap (content-hash diff)."""
        keys = self._keys()
        if keys is None:
            return False
        now = now_ms if now_ms is not None else _now_ms()
        try:
            digests = {
                entry.relative_path: sha256_hex(entry.file.read_bytes()) for entry in scope.scan(self._root)
            }
        except OSError:
            return False

        def transform(state):
            clock = state.hlc
            changed = False
            old_by_path = {entry.relative_path: entry for entry in state.entries}
            new_entries: list[SyncEntry] = []
            for path in sorted(digests):
                digest = digests[path]
                old = old_by_path.get(path)
                if old is None or old.digest != digest:
                    clock = clock.tick(now)
                    new_entries.append(SyncEntry(path, clock, digest))
                    changed = True
                else:
                    new_entries.append(old)
            tombstones = list(state.tombstones)
            for old in state.entries:
                if old.relative_path not in digests:
                    clock = clock.tick(now)
                    tombstones.append(SyncTombstone(old.relative_path, clock, record_id(keys.name_key(), old.relative_path)))
                    changed = True
            if not changed:
                return state
            cleaned = {
                tombstone.relative_path: tombstone
                for tombstone in tombstones
                if tombstone.relative_path not in digests
            }
            return replace(
                state,
                hlc=clock,
                entries=new_entries,
                tombstones=sorted(cleaned.values(), key=lambda tombstone: tombstone.relative_path),
            )

        return self._state_store.update(transform) is not None

    # ── remote apply (secondary side) ───────────────────────────────────────────

    def apply_remote(self, envelopes: list[SyncEnvelope], keys: SyncKeys) -> SyncErrorCode | None:
        """Apply one pulled batch. ``None`` on success; otherwise the error code the
        caller maps to the user-facing message (nothing is partially applied)."""
        state = self._state_store.load()
        if state is None:
            return SyncErrorCode.STATE_CORRUPT
        payloads: list[tuple[SyncEnvelope, SyncPayload]] = []
        for envelope in envelopes:
            payload = unpack(keys, envelope)
            if payload is None:
                return SyncErrorCode.VERIFY
            payloads.append((envelope, payload))
        clock = state.hlc
        now = _now_ms()
        applied = 0
        archived = 0
        entries: dict[str, SyncEntry] = {entry.relative_path: entry for entry in state.entries}
        for envelope, payload in payloads:
            clock = clock.recv(envelope.hlc, now)
            existing = entries.get(payload.relative_path)
            if payload.deleted:
                # Only delete files this device knows to be sync mirrors; a local-only
                # file the master never recorded is never touched.
                if existing is not None and existing.hlc < envelope.hlc:
                    if archive_and_delete(self._root, payload.relative_path):
                        del entries[payload.relative_path]
                        applied += 1
            else:
                if existing is not None and envelope.hlc <= existing.hlc:
                    continue
                content = payload.content or ""
                local = read_current(self._root, payload.relative_path)
                if local is not None and sha256_hex(local.encode("utf-8")) == envelope.digest:
                    pass  # local content already matches: only refresh the cache
                else:
                    if local is not None:
                        archived += 1
                    if not apply_sync_file(self._root, payload.relative_path, content):
                        continue
                    applied += 1
                entries[payload.relative_path] = SyncEntry(payload.relative_path, envelope.hlc, envelope.digest)
        saved = self._state_store.save(
            replace(
                state,
                hlc=clock,
                entries=sorted(entries.values(), key=lambda entry: entry.relative_path),
                applied_count=state.applied_count + applied,
                archived_count=state.archived_count + archived,
                last_sync_at_ms=now,
                last_sync_error=None,
            )
        )
        return None if saved else SyncErrorCode.STATE_CORRUPT

    def compute_outgoing(self, remote: list[SyncEnvelope], keys: SyncKeys) -> list[SyncEnvelope]:
        """The uplink side of the two-way negotiation (Android v2.8): diff the master's
        manifest per path by HLC and pack the locally-newer records (including
        tombstones). The local log refreshes first so unsynced local edits join the
        comparison; a remote envelope that fails to unpack is skipped, not fatal."""
        self.refresh_from_local_changes()
        state = self._state_store.load()
        if state is None:
            return []
        remote_hlc_by_path: dict[str, SyncClock] = {}
        for envelope in remote:
            payload = unpack(keys, envelope)
            if payload is None:
                continue
            known = remote_hlc_by_path.get(payload.relative_path)
            if known is None or envelope.hlc > known:
                remote_hlc_by_path[payload.relative_path] = envelope.hlc
        outgoing: list[SyncEnvelope] = []
        for entry in state.entries:
            remote_hlc = remote_hlc_by_path.get(entry.relative_path)
            if remote_hlc is not None and entry.hlc <= remote_hlc:
                continue
            content = read_current(self._root, entry.relative_path)
            if content is None:
                continue
            envelope = pack(keys, entry.hlc, state.device_id, SyncPayload(entry.relative_path, content))
            if envelope is not None:
                outgoing.append(envelope)
        for tombstone in state.tombstones:
            remote_hlc = remote_hlc_by_path.get(tombstone.relative_path)
            if remote_hlc is not None and tombstone.hlc <= remote_hlc:
                continue
            envelope = pack(keys, tombstone.hlc, state.device_id, SyncPayload(tombstone.relative_path, deleted=True))
            if envelope is not None:
                outgoing.append(envelope)
        return outgoing

    def _keys(self) -> SyncKeys | None:
        words = self._mnemonic_provider()
        if not words:
            return None
        return SyncKeys.from_words(words, self._mnemonic)
