"""BIP39 24-word mnemonic: generate, validate, derive the seed.

Mirrors the Android ``SyncMnemonic.kt``: 256-bit entropy encodes to 24 words with an
8-bit checksum. Generation, validation and seed derivation all happen locally — the
mnemonic is the sole root credential of the whole sync chain (paper backup, unrecoverable
when lost). The wordlist is the standard English 2048-word list; pairing ends must use
the same list.
"""

from __future__ import annotations

import hashlib
import secrets
from pathlib import Path

WORD_COUNT = 24
ENTROPY_BYTES = 32

_CHECKSUM_BITS = 8
_INDEX_BITS = 11
_PBKDF2_ITERATIONS = 2048
_SEED_BYTES = 64
_SALT = b"mnemonic"

_WORDLIST_PATH = Path(__file__).with_name("bip39_english_words.txt")


def load_wordlist(path: Path = _WORDLIST_PATH) -> list[str]:
    """One word per line. Count and boundary words are checked (fail closed): a corrupted
    wordlist must leave sync unusable rather than running half-broken."""
    words = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(words) != 2048 or words[0] != "abandon" or words[-1] != "zoo":
        raise ValueError(f"bip39 wordlist corrupted: {len(words)} entries")
    return words


class SyncMnemonic:
    def __init__(self, wordlist: list[str] | None = None) -> None:
        self._words = wordlist if wordlist is not None else load_wordlist()

    def generate(self) -> list[str]:
        """A fresh 24-word mnemonic (256-bit entropy)."""
        entropy = secrets.token_bytes(ENTROPY_BYTES)
        return self._encode(entropy)

    def validate(self, words: list[str]) -> bool:
        return self.to_entropy(words) is not None

    def to_entropy(self, words: list[str]) -> bytes | None:
        """Recover the entropy; wrong word count, an unknown word or a bad checksum
        returns ``None`` (pairing is refused, HM-6002)."""
        if len(words) != WORD_COUNT:
            return None
        bits: list[bool] = [False] * (WORD_COUNT * _INDEX_BITS)
        for i, word in enumerate(words):
            index = self._word_index(word)
            if index is None:
                return None
            for bit in range(_INDEX_BITS):
                bits[i * _INDEX_BITS + bit] = bool(index & (1 << (_INDEX_BITS - 1 - bit)))
        entropy = bytearray(ENTROPY_BYTES)
        for i in range(ENTROPY_BYTES * 8):
            if bits[i]:
                entropy[i // 8] |= 0x80 >> (i % 8)
        digest = hashlib.sha256(bytes(entropy)).digest()
        for i in range(_CHECKSUM_BITS):
            expected = bool(digest[0] & (0x80 >> (i % 8)))
            if bits[ENTROPY_BYTES * 8 + i] != expected:
                return None
        return bytes(entropy)

    def to_seed(self, words: list[str]) -> bytes:
        """BIP39 seed: PBKDF2-HMAC-SHA512, 2048 rounds, fixed salt ``mnemonic`` (no passphrase).

        The 2048 count is BIP39-mandated, not a password-hashing work factor: the root
        credential is 256 bits of entropy, so both ends must derive identically — raising
        it here would break interop with the Android client, not add security.
        """
        normalized = " ".join(word.strip().lower() for word in words)
        return hashlib.pbkdf2_hmac("sha512", normalized.encode("utf-8"), _SALT, _PBKDF2_ITERATIONS, dklen=_SEED_BYTES)

    def _encode(self, entropy: bytes) -> list[str]:
        digest = hashlib.sha256(entropy).digest()
        bits: list[bool] = [False] * (ENTROPY_BYTES * 8 + _CHECKSUM_BITS)
        for i in range(ENTROPY_BYTES * 8):
            bits[i] = bool(entropy[i // 8] & (0x80 >> (i % 8)))
        for i in range(_CHECKSUM_BITS):
            bits[ENTROPY_BYTES * 8 + i] = bool(digest[0] & (0x80 >> (i % 8)))
        words: list[str] = []
        for i in range(WORD_COUNT):
            index = 0
            for bit in range(_INDEX_BITS):
                index = (index << 1) | (1 if bits[i * _INDEX_BITS + bit] else 0)
            words.append(self._words[index])
        return words

    def _word_index(self, word: str) -> int | None:
        try:
            return self._words.index(word.strip().lower())
        except ValueError:
            return None
