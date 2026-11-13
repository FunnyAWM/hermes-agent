"""Per-device sync state, persisted at ``<sync root>/.meta/state.json``.

Mirrors the Android ``SyncStateStore.kt``: device id, hybrid clock, generation, tombstones
and the record log, plus the TOFU-pinned master fingerprint and sync counters. A parse
failure is surfaced separately from "unpaired" (fail closed, HM-6004) — the caller must
never treat a corrupt state as "not paired yet". Writes go through a temp file + atomic
rename so no half file remains.

The JSON schema mirrors the Kotlin serialization (camelCase); unlike the wire format the
file is device-local, so encoding always writes every field while decoding accepts
absent defaults exactly like kotlinx does.
"""

from __future__ import annotations

import json
import os
import secrets
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

from sync.clock import SyncClock
from sync.keys import INITIAL_GENERATION
from sync.records import strict_json_object

FILE_NAME = "state.json"


@dataclass(frozen=True, slots=True)
class SyncTombstone:
    relative_path: str
    hlc: SyncClock
    record_id: str


@dataclass(frozen=True, slots=True)
class SyncEntry:
    relative_path: str
    hlc: SyncClock
    digest: str


@dataclass(frozen=True, slots=True)
class SyncState:
    device_id: str
    hlc: SyncClock
    generation: int = INITIAL_GENERATION
    tombstones: list[SyncTombstone] = field(default_factory=list)
    entries: list[SyncEntry] = field(default_factory=list)
    #: SHA-256 hex of the master certificate pinned on first use; ``None`` = not pinned yet.
    pinned_fingerprint: str | None = None
    last_sync_at_ms: int | None = None
    last_sync_error: str | None = None
    applied_count: int = 0
    archived_count: int = 0


def _encode_clock(clock: SyncClock) -> dict[str, Any]:
    return {"wallMs": clock.wall_ms, "counter": clock.counter, "deviceId": clock.device_id}


def _decode_clock(obj: Any) -> SyncClock | None:
    if not isinstance(obj, dict) or set(obj) != {"wallMs", "counter", "deviceId"}:
        return None
    wall_ms, counter = obj["wallMs"], obj["counter"]
    device_id = obj["deviceId"]
    ints_ok = isinstance(wall_ms, int) and isinstance(counter, int) and not isinstance(wall_ms, bool) and not isinstance(counter, bool)
    if not ints_ok or not isinstance(device_id, str):
        return None
    return SyncClock(wall_ms, counter, device_id)


def encode_state(state: SyncState) -> dict[str, Any]:
    return {
        "deviceId": state.device_id,
        "hlc": _encode_clock(state.hlc),
        "generation": state.generation,
        "tombstones": [
            {"relativePath": tombstone.relative_path, "hlc": _encode_clock(tombstone.hlc), "recordId": tombstone.record_id}
            for tombstone in state.tombstones
        ],
        "entries": [
            {"relativePath": entry.relative_path, "hlc": _encode_clock(entry.hlc), "digest": entry.digest}
            for entry in state.entries
        ],
        "pinnedFingerprint": state.pinned_fingerprint,
        "lastSyncAtMs": state.last_sync_at_ms,
        "lastSyncError": state.last_sync_error,
        "appliedCount": state.applied_count,
        "archivedCount": state.archived_count,
    }


def _decode_tombstone(obj: Any) -> SyncTombstone | None:
    if not isinstance(obj, dict) or set(obj) != {"relativePath", "hlc", "recordId"}:
        return None
    hlc = _decode_clock(obj["hlc"])
    if hlc is None or not isinstance(obj["relativePath"], str) or not isinstance(obj["recordId"], str):
        return None
    return SyncTombstone(obj["relativePath"], hlc, obj["recordId"])


def _decode_entry(obj: Any) -> SyncEntry | None:
    if not isinstance(obj, dict) or set(obj) != {"relativePath", "hlc", "digest"}:
        return None
    hlc = _decode_clock(obj["hlc"])
    if hlc is None or not isinstance(obj["relativePath"], str) or not isinstance(obj["digest"], str):
        return None
    return SyncEntry(obj["relativePath"], hlc, obj["digest"])


def decode_state(obj: dict[str, Any]) -> SyncState | None:
    if not set(obj) <= {
        "deviceId",
        "hlc",
        "generation",
        "tombstones",
        "entries",
        "pinnedFingerprint",
        "lastSyncAtMs",
        "lastSyncError",
        "appliedCount",
        "archivedCount",
    } or not {"deviceId", "hlc"} <= set(obj):
        return None
    hlc = _decode_clock(obj["hlc"])
    if hlc is None or not isinstance(obj["deviceId"], str):
        return None
    tombstones_obj = obj.get("tombstones", [])
    entries_obj = obj.get("entries", [])
    if not isinstance(tombstones_obj, list) or not isinstance(entries_obj, list):
        return None
    tombstones = []
    for tombstone_obj in tombstones_obj:
        tombstone = _decode_tombstone(tombstone_obj)
        if tombstone is None:
            return None
        tombstones.append(tombstone)
    entries = []
    for entry_obj in entries_obj:
        entry = _decode_entry(entry_obj)
        if entry is None:
            return None
        entries.append(entry)
    ints_ok = all(
        isinstance(obj.get(key, 0), int) and not isinstance(obj.get(key, 0), bool)
        for key in ("generation", "appliedCount", "archivedCount")
    )
    stamps_ok = all(
        obj.get(key) is None or isinstance(obj.get(key), int)
        for key in ("lastSyncAtMs",)
    )
    strings_ok = obj.get("pinnedFingerprint") is None or isinstance(obj["pinnedFingerprint"], str)
    strings_ok = strings_ok and (obj.get("lastSyncError") is None or isinstance(obj["lastSyncError"], str))
    if not ints_ok or not stamps_ok or not strings_ok:
        return None
    return SyncState(
        device_id=obj["deviceId"],
        hlc=hlc,
        generation=obj.get("generation", INITIAL_GENERATION),
        tombstones=tombstones,
        entries=entries,
        pinned_fingerprint=obj.get("pinnedFingerprint"),
        last_sync_at_ms=obj.get("lastSyncAtMs"),
        last_sync_error=obj.get("lastSyncError"),
        applied_count=obj.get("appliedCount", 0),
        archived_count=obj.get("archivedCount", 0),
    )


class SyncStateStore:
    def __init__(self, meta_dir: Path) -> None:
        self.meta_dir = Path(meta_dir)
        self.state_file = self.meta_dir / FILE_NAME

    def exists(self) -> bool:
        return self.state_file.is_file()

    def load(self) -> SyncState | None:
        """The stored state; ``None`` whether the file is missing or corrupt — callers
        that must tell the two apart check :meth:`exists` first (HM-6004)."""
        try:
            payload = self.state_file.read_bytes()
        except OSError:
            return None
        obj = strict_json_object(payload)
        if obj is None:
            return None
        return decode_state(obj)

    def create(self) -> SyncState | None:
        """A fresh state for first pairing (random device id), persisted immediately."""
        device_id = secrets.token_bytes(16).hex()
        return self.save(SyncState(device_id=device_id, hlc=SyncClock(0, 0, device_id)))

    def save(self, state: SyncState) -> SyncState | None:
        try:
            self.meta_dir.mkdir(parents=True, exist_ok=True)
            handle, temp_name = tempfile.mkstemp(prefix=".state-", suffix=".tmp", dir=self.meta_dir)
            temp_file = Path(temp_name)
            try:
                with os.fdopen(handle, "wb") as stream:
                    stream.write(json.dumps(encode_state(state), ensure_ascii=False, indent=2).encode("utf-8"))
                os.replace(temp_file, self.state_file)
            finally:
                temp_file.unlink(missing_ok=True)
        except OSError:
            return None
        return state

    def update(self, transform: Callable[[SyncState], SyncState]) -> SyncState | None:
        """Read-modify-write; ``None`` on a corrupt state or a failed save (HM-6004)."""
        current = self.load()
        if current is None:
            return None
        return self.save(transform(current))

    def clear(self) -> bool:
        try:
            if not self.state_file.exists():
                return True
            self.state_file.unlink()
            return True
        except OSError:
            return False


def with_fields(state: SyncState, **changes: Any) -> SyncState:
    """Convenience wrapper over :func:`dataclasses.replace` for ``update`` transforms."""
    return replace(state, **changes)
