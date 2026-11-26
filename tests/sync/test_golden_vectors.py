"""Golden vectors for the KouriChat Link crypto contract.

The Kotlin suite (``SyncKeyDerivationTest``) asserts the same RFC 5869 case and the same
mnemonic vectors, so a Python/Kotlin derivation drift fails here first. The BIP39 seed
vector is the official empty-passphrase entry for ``abandon``×11 + ``about``
(trezor/python-mnemonic ``vectors.json``, referenced by BIP 39).
"""

from __future__ import annotations

import hashlib
import hmac

import pytest

from sync.keys import _hkdf
from sync.mnemonic import SyncMnemonic, load_wordlist

RFC5869_CASE1_OKM = "3cb25f25faacd57a90434f64d0362f2a2d2d0a90cf1a5a4c5db02d56ecc4c5bf34007208d5b887185865"
BIP39_ABANDON12_SEED = (
    "5eb00bbddcf069084889a8ab9155568165f5c453ccb85e70811aaed6f6da5fc1"
    "9a5ac40b389cd370d086206dec8aa6c43daea6690f20ad3d8d48b2d2ce9e38e4"
)

#: The two mnemonics the Kotlin ``SyncKeyDerivationTest`` uses.
KOTLIN_VECTOR_1 = ["abandon"] * 23 + ["art"]
KOTLIN_VECTOR_2 = [
    "abandon", "amount", "liar", "amount", "expire", "adjust", "cage", "candy", "arch", "gather",
    "drum", "bullet", "absurd", "math", "era", "live", "bid", "rhythm", "alien", "crouch",
    "range", "attend", "journey", "unaware",
]


def test_hkdf_matches_rfc5869_case1():
    okm = _hkdf(
        ikm=b"\x0b" * 22,
        salt=bytes(range(13)),
        info=bytes(range(0xF0, 0xFA)),
        length=42,
    )
    assert okm.hex() == RFC5869_CASE1_OKM


def test_pbkdf2_matches_official_bip39_vector():
    mnemonic = SyncMnemonic()
    seed = mnemonic.to_seed(["abandon"] * 11 + ["about"])
    assert seed.hex() == BIP39_ABANDON12_SEED
    # Independent primitive cross-check: hashlib IS the reference implementation.
    assert seed == hashlib.pbkdf2_hmac(
        "sha512",
        ("abandon " * 11 + "about").encode("utf-8"),
        b"mnemonic",
        2048,
        dklen=64,
    )


@pytest.mark.parametrize("words", [KOTLIN_VECTOR_1, KOTLIN_VECTOR_2], ids=["vector1", "vector2"])
def test_kotlin_test_vectors_validate(words):
    mnemonic = SyncMnemonic()
    assert mnemonic.validate(words)
    assert mnemonic.to_entropy(words) is not None
    assert SyncMnemonic().to_seed(words)


@pytest.mark.parametrize(
    "words",
    [
        ["notaword"] * 24,
        [],
        ["abandon"] * 23 + ["about"],  # valid words, broken checksum
        KOTLIN_VECTOR_1[:-1],  # wrong word count
    ],
)
def test_invalid_words_are_rejected(words):
    assert not SyncMnemonic().validate(words)


def test_generate_produces_valid_distinct_chains():
    mnemonic = SyncMnemonic()
    first = mnemonic.generate()
    second = mnemonic.generate()
    assert mnemonic.validate(first) and mnemonic.validate(second)
    assert first != second
    assert all(mnemonic.to_seed(words) for words in (first, second))


def test_wordlist_integrity_fail_closed():
    words = load_wordlist()
    assert len(words) == 2048
    assert words[0] == "abandon" and words[-1] == "zoo"


def test_hmac_hex_parts_are_pipe_joined():
    key = b"\x01" * 32
    joined = hmac.new(key, b"record|memories/MEMORY.md", hashlib.sha256).hexdigest()
    from sync.crypto import hmac_hex

    assert hmac_hex(key, ["record", "memories/MEMORY.md"]) == joined
