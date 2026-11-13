"""Sync payload (plaintext domain) and envelope (ciphertext domain).

Mirrors the Android ``SyncRecord.kt``: the real path exists only inside the payload; the
wire and any storage expose an opaque record id (HMAC of the path under the name key, so
record ids never reveal paths). Ciphertext is ``Base64(IV + AES-256-GCM)`` with the AAD
binding record id + clock + device id, so a ciphertext cannot be replayed under another
name. AAD mismatch, digest mismatch or a corrupt payload all fail the unpack — the
caller rejects the whole batch (HM-6003).

Wire JSON mirrors kotlinx `Json` defaults exactly: defaults are *omitted* when encoding
(``content``/``deleted``/``envelopes`` may be absent) and *unknown keys are rejected*
when decoding — both ends fail closed together.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from typing import Any

from sync.clock import SyncClock
from sync.crypto import encrypt, decrypt, hmac_hex, sha256_hex
from sync.keys import SyncKeys

MAX_PLAINTEXT_BYTES = 20 * 1024 * 1024

_RECORD_DOMAIN = "record"


@dataclass(frozen=True, slots=True)
class SyncPayload:
    relative_path: str
    content: str | None = None
    deleted: bool = False


@dataclass(frozen=True, slots=True)
class SyncEnvelope:
    record_id: str
    generation: int
    hlc: SyncClock
    device_id: str
    digest: str
    size: int
    ciphertext: str


def record_id(name_key: bytes, relative_path: str) -> str:
    return hmac_hex(name_key, [_RECORD_DOMAIN, relative_path])


def aad_of(record_id_hex: str, hlc: SyncClock, device_id: str) -> bytes:
    return f"{record_id_hex}|{hlc.canonical()}|{device_id}".encode()


def strict_json_object(data: bytes) -> dict[str, Any] | None:
    """Decode a wire JSON object: reject duplicates and non-object roots so both ends
    fail closed on the same inputs kotlinx would refuse."""
    try:
        decoded = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeDecodeError, ValueError):
        return None
    return decoded if isinstance(decoded, dict) else None


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f"duplicate JSON key: {key}")
        obj[key] = value
    return obj


def encode_payload(payload: SyncPayload) -> bytes:
    obj: dict[str, Any] = {"relativePath": payload.relative_path}
    if payload.content is not None:
        obj["content"] = payload.content
    if payload.deleted:
        obj["deleted"] = True
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def decode_payload(obj: Any) -> SyncPayload | None:
    if not isinstance(obj, dict) or not set(obj) <= {"relativePath", "content", "deleted"}:
        return None
    relative_path = obj.get("relativePath")
    content = obj.get("content")
    deleted = obj.get("deleted", False)
    if not isinstance(relative_path, str):
        return None
    if content is not None and not isinstance(content, str):
        return None
    if not isinstance(deleted, bool):
        return None
    return SyncPayload(relative_path, content, deleted)


def encode_envelope(envelope: SyncEnvelope) -> dict[str, Any]:
    return {
        "recordId": envelope.record_id,
        "generation": envelope.generation,
        "hlc": {
            "wallMs": envelope.hlc.wall_ms,
            "counter": envelope.hlc.counter,
            "deviceId": envelope.hlc.device_id,
        },
        "deviceId": envelope.device_id,
        "digest": envelope.digest,
        "size": envelope.size,
        "ciphertext": envelope.ciphertext,
    }


def decode_envelope(obj: Any) -> SyncEnvelope | None:
    if not isinstance(obj, dict) or not set(obj) == {
        "recordId",
        "generation",
        "hlc",
        "deviceId",
        "digest",
        "size",
        "ciphertext",
    }:
        return None
    hlc_obj = obj["hlc"]
    if not isinstance(hlc_obj, dict) or set(hlc_obj) != {"wallMs", "counter", "deviceId"}:
        return None
    ints_ok = all(
        isinstance(value, int) and not isinstance(value, bool)
        for value in (obj["generation"], obj["size"], hlc_obj["wallMs"], hlc_obj["counter"])
    )
    strings_ok = all(
        isinstance(obj[key], str) for key in ("recordId", "deviceId", "digest", "ciphertext")
    ) and isinstance(hlc_obj["deviceId"], str)
    if not ints_ok or not strings_ok:
        return None
    return SyncEnvelope(
        record_id=obj["recordId"],
        generation=obj["generation"],
        hlc=SyncClock(hlc_obj["wallMs"], hlc_obj["counter"], hlc_obj["deviceId"]),
        device_id=obj["deviceId"],
        digest=obj["digest"],
        size=obj["size"],
        ciphertext=obj["ciphertext"],
    )


def pack(keys: SyncKeys, hlc: SyncClock, device_id: str, payload: SyncPayload) -> SyncEnvelope | None:
    """An over-limit payload returns ``None``; the caller surfaces it in the sync page
    counters instead of silently dropping it."""
    content_bytes = (payload.content or "").encode("utf-8")
    if len(content_bytes) > MAX_PLAINTEXT_BYTES:
        return None
    id_hex = record_id(keys.name_key(), payload.relative_path)
    plaintext = encode_payload(payload)
    ciphertext = encrypt(keys.content_key(keys.generation), plaintext, aad_of(id_hex, hlc, device_id))
    return SyncEnvelope(
        record_id=id_hex,
        generation=keys.generation,
        hlc=hlc,
        device_id=device_id,
        digest=sha256_hex(content_bytes),
        size=len(plaintext),
        ciphertext=base64.b64encode(ciphertext).decode("ascii"),
    )


def unpack(keys: SyncKeys, envelope: SyncEnvelope) -> SyncPayload | None:
    """AAD mismatch, digest mismatch or a corrupt payload returns ``None`` (HM-6003)."""
    try:
        ciphertext = base64.b64decode(envelope.ciphertext, validate=True)
    except (binascii.Error, ValueError):
        return None
    plaintext = decrypt(
        keys.content_key(envelope.generation),
        ciphertext,
        aad_of(envelope.record_id, envelope.hlc, envelope.device_id),
    )
    if plaintext is None:
        return None
    payload = decode_payload(strict_json_object(plaintext))
    if payload is None:
        return None
    content_bytes = (payload.content or "").encode("utf-8")
    if sha256_hex(content_bytes) != envelope.digest:
        return None
    return payload
