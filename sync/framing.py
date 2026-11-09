"""Length-prefixed message framing for the KouriChat Link wire protocol.

Mirrors the Android ``SyncFraming.kt``: every frame is a 4-byte big-endian length followed
by exactly that many payload bytes. A frame whose declared length exceeds
:data:`MAX_FRAME_BYTES` aborts the stream (fail closed) so a peer cannot exhaust memory
with a single oversized length claim; the cap covers the Base64 expansion of a 20 MiB
record. A 0-length frame is valid and decodes as an empty payload.
"""

from __future__ import annotations

import struct
from typing import IO

MAX_FRAME_BYTES = 32 * 1024 * 1024

_LENGTH_BYTES = 4
_HEADER = struct.Struct(">I")


class FrameTooLargeError(Exception):
    """A declared or actual frame length exceeds :data:`MAX_FRAME_BYTES` (protocol error)."""


def write_frame(output: IO[bytes], payload: bytes) -> None:
    if len(payload) > MAX_FRAME_BYTES:
        raise FrameTooLargeError(f"frame too large: {len(payload)}")
    output.write(_HEADER.pack(len(payload)))
    output.write(payload)
    # Buffered sinks need the flush; raw/SSL sockets have none (writes are immediate).
    flush = getattr(output, "flush", None)
    if flush is not None:
        flush()


def read_frame(reader: IO[bytes]) -> bytes | None:
    """Read one frame; ``None`` on orderly stream end. A length claim beyond the cap or a
    truncated frame raises: the caller treats both as a protocol error and disconnects."""
    header = _read_exactly(reader, _LENGTH_BYTES)
    if header is None:
        return None
    (length,) = _HEADER.unpack(header)
    if length > MAX_FRAME_BYTES:
        raise FrameTooLargeError(f"frame length beyond limit: {length}")
    if length == 0:
        return b""
    payload = _read_exactly(reader, length)
    if payload is None:
        raise EOFError("frame truncated")
    return payload


def _read_exactly(reader: IO[bytes], length: int) -> bytes | None:
    buffer = bytearray()
    while len(buffer) < length:
        chunk = reader.read(length - len(buffer))
        if not chunk:
            return None
        buffer.extend(chunk)
    return bytes(buffer)
