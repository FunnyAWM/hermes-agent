"""Wire messages and protocol constants.

Mirrors the Android ``SyncProtocol.kt`` (v2.8 two-way negotiation contract). Handshake
order: hello → challenge → auth → auth_ok; then ``list`` exchanges full manifests and the
client may ``push`` its locally-newer records (newest-HLC-wins merge decided by data, no
interactive arbitration); any mismatch disconnects, and no record travels before
authentication succeeds. The HMAC proof only demonstrates knowledge of the mnemonic — no
derived key ever travels on the wire.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field
from typing import Any

from sync.framing import read_frame, write_frame
from sync.records import SyncEnvelope, decode_envelope, encode_envelope, strict_json_object
from sync.crypto import hmac_hex

TYPE_HELLO = "hello"
TYPE_CHALLENGE = "challenge"
TYPE_AUTH = "auth"
TYPE_AUTH_OK = "auth_ok"
TYPE_AUTH_FAIL = "auth_fail"
TYPE_LIST = "list"
TYPE_LIST_RESULT = "list_result"
TYPE_FETCH = "fetch"
TYPE_RECORD = "record"
TYPE_PUSH = "push"
TYPE_PUSH_OK = "push_ok"
TYPE_ERROR = "error"
TYPE_BYE = "bye"

#: Fixed sync service port (Android VALIDATION §2); bound on all interfaces, LAN only.
DEFAULT_PORT = 27890

#: Per-read handshake wait ceiling so a half-open connection cannot hold a thread.
HANDSHAKE_TIMEOUT_MS = 15_000

NONCE_BYTES = 16

_PROOF_DOMAIN = "kourichat-sync-v1"

_STR_FIELDS = ("deviceId", "nonceClient", "nonceServer", "proof", "reason", "recordId")


@dataclass(slots=True)
class SyncMessage:
    type: str
    device_id: str | None = None
    nonce_client: str | None = None
    nonce_server: str | None = None
    proof: str | None = None
    reason: str | None = None
    record_id: str | None = None
    envelopes: list[SyncEnvelope] = field(default_factory=list)


def proof(auth_key: bytes, nonce_client: str, nonce_server: str) -> str:
    return hmac_hex(auth_key, [_PROOF_DOMAIN, nonce_client, nonce_server])


def new_nonce() -> str:
    return secrets.token_bytes(NONCE_BYTES).hex()


def encode_message(message: SyncMessage) -> bytes:
    obj: dict[str, Any] = {"type": message.type}
    if message.device_id is not None:
        obj["deviceId"] = message.device_id
    if message.nonce_client is not None:
        obj["nonceClient"] = message.nonce_client
    if message.nonce_server is not None:
        obj["nonceServer"] = message.nonce_server
    if message.proof is not None:
        obj["proof"] = message.proof
    if message.reason is not None:
        obj["reason"] = message.reason
    if message.record_id is not None:
        obj["recordId"] = message.record_id
    if message.envelopes:
        obj["envelopes"] = [encode_envelope(envelope) for envelope in message.envelopes]
    return _dumps(obj)


def decode_message(payload: bytes) -> SyncMessage | None:
    """Unknown keys, duplicates or type mismatches return ``None`` — the caller
    disconnects (HM-6003), mirroring kotlinx's strict decoding."""
    obj = strict_json_object(payload)
    if obj is None or "type" not in obj:
        return None
    if not isinstance(obj["type"], str):
        return None
    if not set(obj) <= {"type", *_STR_FIELDS, "envelopes"}:
        return None
    for key in _STR_FIELDS:
        if key in obj and not isinstance(obj[key], str):
            return None
    envelopes_obj = obj.get("envelopes", [])
    if not isinstance(envelopes_obj, list):
        return None
    envelopes = []
    for envelope_obj in envelopes_obj:
        envelope = decode_envelope(envelope_obj)
        if envelope is None:
            return None
        envelopes.append(envelope)
    return SyncMessage(
        type=obj["type"],
        device_id=obj.get("deviceId"),
        nonce_client=obj.get("nonceClient"),
        nonce_server=obj.get("nonceServer"),
        proof=obj.get("proof"),
        reason=obj.get("reason"),
        record_id=obj.get("recordId"),
        envelopes=envelopes,
    )


def read_message(reader) -> SyncMessage | None:
    payload = read_frame(reader)
    if payload is None:
        return None
    return decode_message(payload)


def write_message(writer, message: SyncMessage) -> None:
    write_frame(writer, encode_message(message))


def _dumps(obj: dict[str, Any]) -> bytes:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
