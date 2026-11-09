"""Envelope crypto and digests.

Mirrors the Android ``SyncCrypto.kt``: AES-256-GCM with a random 12-byte IV prepended to
the ciphertext, SHA-256 hex digests, and HMAC over UTF-8 parts joined by ``|`` (fed
segment-wise on the Kotlin side, which is byte-identical to joining first) so parameter
concatenation can never be ambiguous.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_IV_BYTES = 12


def encrypt(key: bytes, plaintext: bytes, aad: bytes) -> bytes:
    iv = secrets.token_bytes(_IV_BYTES)
    return iv + AESGCM(key).encrypt(iv, plaintext, aad)


def decrypt(key: bytes, data: bytes, aad: bytes) -> bytes | None:
    """Any failure returns ``None`` (fail closed); the caller maps it to HM-6003."""
    try:
        if len(data) <= _IV_BYTES:
            return None
        return AESGCM(key).decrypt(data[:_IV_BYTES], data[_IV_BYTES:], aad)
    except (InvalidTag, ValueError):
        return None


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hmac_hex(key: bytes, parts: list[str]) -> str:
    return hmac.new(key, "|".join(parts).encode("utf-8"), hashlib.sha256).hexdigest()
