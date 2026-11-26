"""Pairing QR payload (Android 同 01 / 同 11): ``kourichat-sync:v1|24 words|host|port``.

Mirrors the Android ``SyncQr.parse`` — the PC has no camera, but a phone shows this QR
and any scanner turns it into text; ``hermes sync pair --qr "<payload>"`` accepts that
text directly so pairing needs no manual address entry.
"""

from __future__ import annotations

from dataclasses import dataclass

PAYLOAD_PREFIX = "kourichat-sync:v1"


@dataclass(frozen=True, slots=True)
class QrPairing:
    words: list[str]
    host: str
    port: int


def parse(payload: str) -> QrPairing | None:
    """Parse one pairing payload; any shape mismatch returns ``None``."""
    parts = payload.strip().split("|")
    if len(parts) != 4 or parts[0] != PAYLOAD_PREFIX:
        return None
    words = [word for word in parts[1].split(" ") if word]
    host = parts[2]
    try:
        port = int(parts[3])
    except ValueError:
        return None
    if not words or not host or not 1 <= port <= 65535:
        return None
    return QrPairing(words, host, port)
