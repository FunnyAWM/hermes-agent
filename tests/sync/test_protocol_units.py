"""Protocol unit tests: framing, the hybrid logical clock, records and wire messages.

The wire-format tests pin the kotlinx-compatible JSON contract: defaults are omitted on
encode (``content``/``deleted``/``envelopes``) and unknown keys are rejected on decode —
both directions fail closed together with the Android client.
"""

from __future__ import annotations

import io
import json
import threading
from dataclasses import replace as dc_replace

import pytest

from sync.clock import SyncClock
from sync.crypto import decrypt, encrypt, sha256_hex
from sync.errors import SyncErrorCode, SyncError
from sync.framing import FrameTooLargeError, MAX_FRAME_BYTES, read_frame, write_frame
from sync.keys import INITIAL_GENERATION, SyncKeys
from sync.mnemonic import SyncMnemonic
from sync.protocol import (
    TYPE_HELLO,
    SyncMessage,
    decode_message,
    encode_message,
    read_message,
    write_message,
)
from sync.records import (
    MAX_PLAINTEXT_BYTES,
    SyncEnvelope,
    SyncPayload,
    aad_of,
    decode_envelope,
    decode_payload,
    encode_envelope,
    encode_payload,
    pack,
    record_id,
    unpack,
)
from sync.state_store import SyncEntry, SyncStateStore, SyncTombstone, decode_state, encode_state

WORDS = ["abandon"] * 23 + ["art"]
A_PATH = "notes/a.md"


@pytest.fixture(scope="module")
def keys() -> SyncKeys:
    return SyncKeys.from_words(WORDS, SyncMnemonic())


# ── framing ──────────────────────────────────────────────────────────────────────


class TestFraming:
    def test_roundtrip_and_zero_length(self):
        stream = io.BytesIO()
        write_frame(stream, b"hello")
        write_frame(stream, b"")
        write_frame(stream, b"x" * 3)
        stream.seek(0)
        assert read_frame(stream) == b"hello"
        assert read_frame(stream) == b""
        assert read_frame(stream) == b"x" * 3
        assert read_frame(stream) is None

    def test_write_beyond_cap_raises(self):
        with pytest.raises(FrameTooLargeError):
            write_frame(io.BytesIO(), b"x" * (MAX_FRAME_BYTES + 1))

    def test_read_length_claim_beyond_cap_raises(self):
        stream = io.BytesIO((MAX_FRAME_BYTES + 1).to_bytes(4, "big"))
        with pytest.raises(FrameTooLargeError):
            read_frame(stream)

    def test_truncated_frame_raises(self):
        stream = io.BytesIO((10).to_bytes(4, "big") + b"abc")
        with pytest.raises(EOFError):
            read_frame(stream)


class TestFrameErrorMapping:
    """Protocol-level framing errors surface as HM-6003 so the server thread and the sync
    loop see one error type instead of a bare framing exception."""

    def test_length_claim_beyond_cap_maps_to_hm6003(self):
        stream = io.BytesIO((MAX_FRAME_BYTES + 1).to_bytes(4, "big"))
        with pytest.raises(SyncError) as error:
            read_message(stream)
        assert error.value.code is SyncErrorCode.VERIFY

    def test_truncated_frame_maps_to_hm6003(self):
        stream = io.BytesIO((10).to_bytes(4, "big") + b"abc")
        with pytest.raises(SyncError) as error:
            read_message(stream)
        assert error.value.code is SyncErrorCode.VERIFY

    def test_oversize_write_maps_to_hm6003(self, monkeypatch):
        monkeypatch.setattr("sync.protocol.encode_message", lambda message: b"x" * (MAX_FRAME_BYTES + 1))
        with pytest.raises(SyncError) as error:
            write_message(io.BytesIO(), SyncMessage(TYPE_HELLO))
        assert error.value.code is SyncErrorCode.VERIFY


# ── clock ────────────────────────────────────────────────────────────────────────


class TestClock:
    def test_tick_resets_on_advance_and_increments_same_ms(self):
        ticked = SyncClock(100, 0, "d").tick(200)
        assert ticked == SyncClock(200, 0, "d")
        assert ticked.tick(200) == SyncClock(200, 1, "d")
        assert ticked.tick(200).tick(200) == SyncClock(200, 2, "d")

    def test_recv_branches(self):
        clock = SyncClock(100, 5, "d")
        # Remote strictly ahead: adopt its wall clock and counter.
        assert clock.recv(SyncClock(300, 2, "e"), 150) == SyncClock(300, 2, "d")
        # Local strictly ahead of the remote: wall moves to now, counter restarts.
        assert clock.recv(SyncClock(50, 9, "e"), 150) == SyncClock(150, 0, "d")
        # Tie on wall clock: Lamport max+1.
        tied = SyncClock(100, 5, "d").recv(SyncClock(100, 7, "e"), 100)
        assert tied == SyncClock(100, 8, "d")

    def test_recv_never_regresses(self):
        clock = SyncClock(100, 5, "d")
        newer = clock.recv(SyncClock(90, 99, "e"), 50)
        assert newer.wall_ms >= clock.wall_ms

    def test_ordering_is_wall_counter_device(self):
        assert SyncClock(1, 0, "a") < SyncClock(1, 1, "a") < SyncClock(2, 0, "a")
        assert SyncClock(1, 0, "a") < SyncClock(1, 0, "b")
        assert SyncClock(1, 0, "a").canonical() == "1:0:a"


# ── payload / envelope ───────────────────────────────────────────────────────────


def _envelope(keys: SyncKeys) -> SyncEnvelope:
    return pack(keys, SyncClock(10, 0, "d0"), "dev1", SyncPayload(A_PATH, "hello world"))


class TestRecords:
    def test_pack_unpack_roundtrip(self, keys):
        payload = unpack(keys, _envelope(keys))
        assert payload == SyncPayload(A_PATH, "hello world")

    def test_tombstone_roundtrip(self, keys):
        envelope = pack(keys, SyncClock(10, 0, "d0"), "dev1", SyncPayload(A_PATH, deleted=True))
        assert unpack(keys, envelope) == SyncPayload(A_PATH, deleted=True)

    def test_tombstone_digest_is_sha256_of_empty(self, keys):
        envelope = pack(keys, SyncClock(10, 0, "d0"), "dev1", SyncPayload(A_PATH, deleted=True))
        assert envelope.digest == sha256_hex(b"")

    def test_record_id_is_deterministic_and_path_hiding(self, keys):
        first = record_id(keys.name_key(), "memories/MEMORY.md")
        assert first == record_id(keys.name_key(), "memories/MEMORY.md")
        assert first != record_id(keys.name_key(), "memories/USER.md")

    def test_oversize_payload_rejected(self, keys):
        envelope = pack(
            keys,
            SyncClock(10, 0, "d0"),
            "dev1",
            SyncPayload(A_PATH, "x" * (MAX_PLAINTEXT_BYTES + 1)),
        )
        assert envelope is None

    def test_aad_tamper_rejected(self, keys):
        envelope = dc_replace(_envelope(keys), device_id="other-device")
        assert unpack(keys, envelope) is None

    def test_digest_tamper_rejected(self, keys):
        envelope = dc_replace(_envelope(keys), digest="0" * 64)
        assert unpack(keys, envelope) is None

    def test_ciphertext_tamper_rejected(self, keys):
        envelope = _envelope(keys)
        raw = bytearray(envelope.ciphertext.encode("ascii"))
        raw[-2] = ord("A") if raw[-2] == ord("B") else ord("B")
        tampered = dc_replace(envelope, ciphertext=bytes(raw).decode("ascii"))
        assert unpack(keys, tampered) is None

    def test_generation_isolation(self, keys):
        envelope = _envelope(keys)
        ciphertext = _ciphertext_bytes(envelope)
        aad = aad_of(envelope.record_id, envelope.hlc, envelope.device_id)
        assert decrypt(keys.content_key(envelope.generation), ciphertext, aad) is not None
        # Generation keys are isolated: a different generation cannot open this record.
        assert decrypt(keys.content_key(envelope.generation + 1), ciphertext, aad) is None

    def test_record_id_must_bind_the_path(self, keys):
        import base64

        # A chain peer knows the keys, so it can seal the payload under a well-formed record
        # id belonging to another path with a matching AAD: unpack must still reject it.
        base = _envelope(keys)
        claimed = record_id(keys.name_key(), "notes/other.md")
        plaintext = encode_payload(SyncPayload(A_PATH, "hello world"))
        ciphertext = encrypt(keys.content_key(keys.generation), plaintext, aad_of(claimed, base.hlc, base.device_id))
        forged = dc_replace(base, record_id=claimed, ciphertext=base64.b64encode(ciphertext).decode("ascii"))
        assert unpack(keys, forged) is None

    def test_non_initial_generation_rejected(self, keys):
        import base64

        # Even a record that would decrypt under its own generation key is refused: unpack
        # pins the initial generation.
        base = _envelope(keys)
        plaintext = encode_payload(SyncPayload(A_PATH, "hello world"))
        aad = aad_of(base.record_id, base.hlc, base.device_id)
        ciphertext = encrypt(keys.content_key(INITIAL_GENERATION + 1), plaintext, aad)
        forged = dc_replace(
            base,
            generation=INITIAL_GENERATION + 1,
            ciphertext=base64.b64encode(ciphertext).decode("ascii"),
        )
        assert unpack(keys, forged) is None

    def test_payload_json_omits_defaults(self):
        assert json.loads(encode_payload(SyncPayload(A_PATH)).decode("utf-8")) == {"relativePath": A_PATH}
        assert json.loads(encode_payload(SyncPayload(A_PATH, deleted=True)).decode("utf-8")) == {
            "relativePath": A_PATH,
            "deleted": True,
        }

    def test_payload_decode_accepts_missing_defaults(self):
        assert decode_payload({"relativePath": A_PATH}) == SyncPayload(A_PATH)
        assert decode_payload({"relativePath": 1}) is None
        assert decode_payload({"relativePath": A_PATH, "unknown": 1}) is None

    def test_envelope_decode_strict(self, keys):
        envelope = _envelope(keys)
        encoded = encode_envelope(envelope)
        assert decode_envelope(encoded) == envelope
        encoded["unknown"] = 1
        assert decode_envelope(encoded) is None
        del encoded["unknown"]
        del encoded["size"]
        assert decode_envelope(encoded) is None


def _ciphertext_bytes(envelope: SyncEnvelope) -> bytes:
    import base64

    return base64.b64decode(envelope.ciphertext)


# ── wire messages ────────────────────────────────────────────────────────────────


class TestMessage:
    def test_hello_encode_omits_empty_fields(self):
        message = SyncMessage(TYPE_HELLO, device_id="d", nonce_client="nc")
        encoded = json.loads(encode_message(message).decode("utf-8"))
        assert set(encoded) == {"type", "deviceId", "nonceClient"}

    def test_roundtrip(self):
        message = SyncMessage(TYPE_HELLO, device_id="d", nonce_client="nc")
        assert decode_message(encode_message(message)) == message

    def test_strict_decode_rejects_unknown_key(self):
        encoded = json.loads(encode_message(SyncMessage(TYPE_HELLO, device_id="d")).decode("utf-8"))
        encoded["unknown"] = 1
        assert decode_message(json.dumps(encoded).encode("utf-8")) is None

    def test_strict_decode_rejects_malformed(self):
        assert decode_message(b"not json") is None
        assert decode_message(b"[]") is None
        assert decode_message(b'{"type": 1}') is None


# ── state store ──────────────────────────────────────────────────────────────────


class TestStateStore:
    def test_create_load_roundtrip(self, tmp_path):
        store = SyncStateStore(tmp_path / ".meta")
        state = store.create()
        assert state is not None
        assert state.hlc == SyncClock(0, 0, state.device_id)
        loaded = store.load()
        assert loaded == state

    def test_save_persists_entries_and_tombstones(self, tmp_path):
        store = SyncStateStore(tmp_path / ".meta")
        state = store.create()
        clock = state.hlc.tick(1000)
        updated = dc_replace(
            state,
            hlc=clock,
            entries=[SyncEntry("notes/a.md", clock, "d" * 64)],
            tombstones=[SyncTombstone("notes/gone.md", clock, "r" * 64)],
            pinned_fingerprint="f" * 64,
            applied_count=2,
        )
        assert store.save(updated) is not None
        assert store.load() == updated

    def test_corrupt_state_is_not_unpaired(self, tmp_path):
        meta = tmp_path / ".meta"
        meta.mkdir()
        (meta / "state.json").write_text("{nope", encoding="utf-8")
        store = SyncStateStore(meta)
        assert store.exists()
        assert store.load() is None

    def test_missing_state_is_not_corrupt(self, tmp_path):
        store = SyncStateStore(tmp_path / ".meta")
        assert not store.exists()
        assert store.load() is None

    def test_clear(self, tmp_path):
        store = SyncStateStore(tmp_path / ".meta")
        store.create()
        assert store.clear()
        assert not store.exists()

    def test_update_returns_none_on_corrupt(self, tmp_path):
        meta = tmp_path / ".meta"
        meta.mkdir()
        (meta / "state.json").write_text("{nope", encoding="utf-8")
        store = SyncStateStore(meta)
        assert store.update(lambda state: state) is None

    def test_concurrent_updates_lose_no_writes(self, tmp_path):
        store = SyncStateStore(tmp_path / ".meta")
        state = store.create()
        assert state is not None

        def bump() -> None:
            for _ in range(20):
                store.update(lambda current: dc_replace(current, applied_count=current.applied_count + 1))

        workers = [threading.Thread(target=bump) for _ in range(8)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        assert store.load().applied_count == 8 * 20

    def test_encode_decode_json_shape(self, tmp_path):
        store = SyncStateStore(tmp_path / ".meta")
        state = store.create()
        encoded = encode_state(state)
        assert set(encoded) == {
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
        }
        assert decode_state(encoded) == state
