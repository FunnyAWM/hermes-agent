"""Sync key family derived from the mnemonic seed.

Mirrors the Android ``SyncKeyDerivation.kt``: RFC 5869 HKDF-SHA256 with domain-separated
salts. Content keys are generation-isolated (rotation is reserved for the Android 同 07
item; ``generation`` stays 1 until then), while record ids and authentication live on
their own keys. The seed exists only in memory; only the mnemonic ever reaches disk, and
only in the secret store.
"""

from __future__ import annotations

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from sync.mnemonic import SyncMnemonic

KEY_BYTES = 32
INITIAL_GENERATION = 1

_SALT_CONTENT = b"kourichat-sync-content"
_SALT_NAME = b"kourichat-sync-name"
_SALT_AUTH = b"kourichat-sync-auth"


def _hkdf(ikm: bytes, salt: bytes, info: bytes, length: int = KEY_BYTES) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(ikm)


class SyncKeys:
    """Derived sync keys; the seed is kept only for in-memory derivation."""

    def __init__(self, master_seed: bytes) -> None:
        self._master_seed = master_seed
        self.generation = INITIAL_GENERATION

    def content_key(self, generation: int) -> bytes:
        return _hkdf(self._master_seed, _SALT_CONTENT, f"gen:{generation}".encode())

    def name_key(self) -> bytes:
        return _hkdf(self._master_seed, _SALT_NAME, b"record-id")

    def auth_key(self) -> bytes:
        return _hkdf(self._master_seed, _SALT_AUTH, b"challenge")

    @classmethod
    def from_words(cls, words: list[str], mnemonic: SyncMnemonic) -> SyncKeys | None:
        """An invalid word sequence returns ``None``; the caller maps it to HM-6002."""
        if not mnemonic.validate(words):
            return None
        return cls(mnemonic.to_seed(words))
