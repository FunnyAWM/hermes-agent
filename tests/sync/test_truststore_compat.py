"""Truststore-compat regression: the sync link must work inside a hermes process.

hermes injects truststore globally at boot (``agent.ssl_verify.install_truststore``);
on CPython 3.14 the ssl module's ``check_hostname``/``verify_mode`` setters resolve
``SSLContext`` from module globals, so any post-injection assignment recurses into a
stack overflow, and the injected class cannot wrap server-side sockets at all. This file
runs a full master/secondary roundtrip with the injection active. Lives in its own file
because the runner isolates per file and the injection is process-global.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.ssl_verify import install_truststore
from sync.errors import SyncErrorCode
from sync.manager import SyncManager
from sync.mnemonic import SyncMnemonic
from tests.sync.conftest import MemoryMnemonicStore, MemorySettingsStore

WORDS = ["abandon"] * 23 + ["art"]


@pytest.fixture(scope="module", autouse=True)
def _truststore_injected():
    assert install_truststore(), "truststore must be active for this regression"


def _manager(home: Path) -> SyncManager:
    return SyncManager(home, MemorySettingsStore(), MemoryMnemonicStore())


def test_full_pair_roundtrip_with_truststore_injected(tmp_path: Path):
    master = _manager(tmp_path / "master")
    words = master.create_chain()
    (master.root / "notes").mkdir(parents=True)
    (master.root / "notes" / "a.md").write_text("v1", encoding="utf-8")
    master.sync_now()
    server = master.start_server()
    try:
        secondary = _manager(tmp_path / "secondary")
        secondary.join_chain(words, f"127.0.0.1:{server.bound_port}")
        assert (tmp_path / "secondary" / "notes" / "a.md").read_text(encoding="utf-8") == "v1"
        assert secondary.status().pinned_fingerprint

        (master.root / "notes" / "a.md").write_text("v2", encoding="utf-8")
        master.sync_now()
        assert secondary.sync_now().applied == 1
        assert (tmp_path / "secondary" / "notes" / "a.md").read_text(encoding="utf-8") == "v2"
    finally:
        master.stop_server()


def test_wrong_key_still_rejected_with_truststore_injected(tmp_path: Path):
    master = _manager(tmp_path / "master")
    master.create_chain()
    server = master.start_server()
    try:
        from sync.errors import SyncError

        other = [
            "abandon", "amount", "liar", "amount", "expire", "adjust", "cage", "candy", "arch", "gather",
            "drum", "bullet", "absurd", "math", "era", "live", "bid", "rhythm", "alien", "crouch",
            "range", "attend", "journey", "unaware",
        ]
        secondary = _manager(tmp_path / "secondary")
        with pytest.raises(SyncError) as excinfo:
            secondary.join_chain(other, f"127.0.0.1:{server.bound_port}")
        assert excinfo.value.code == SyncErrorCode.AUTH
    finally:
        master.stop_server()


def test_mnemonic_seed_still_matches_after_import_chain():
    # A paranoid canary: the whole import chain above must not disturb derivation.
    assert SyncMnemonic().to_seed(["abandon"] * 11 + ["about"]).hex().startswith("5eb00bbd")
