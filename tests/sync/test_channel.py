"""Channel-level tests: a full master/secondary roundtrip over in-process TLS.

Mirrors the Android ``SyncChannelTest`` on the PC side — convergence through updates and
tombstones, local-only file protection, wrong-key rejection and the TOFU fingerprint
roundtrip — with both ends running in this process on a loopback ephemeral port.
"""

from __future__ import annotations

import socket
import ssl
from dataclasses import replace
from pathlib import Path

import pytest

from sync.client import SyncClient
from sync.errors import SyncError, SyncErrorCode
from sync.identity import FileSyncIdentity
from sync.keys import SyncKeys
from sync.manager import ROLE_DISABLED, ROLE_MASTER, ROLE_SECONDARY, SyncManager, SyncSettings
from sync.mnemonic import SyncMnemonic
from sync.protocol import (
    TYPE_AUTH,
    TYPE_AUTH_OK,
    TYPE_BYE,
    TYPE_CHALLENGE,
    TYPE_FETCH,
    TYPE_HELLO,
    TYPE_LIST,
    TYPE_LIST_RESULT,
    TYPE_RECORD,
    SyncMessage,
    new_nonce,
    proof,
    read_message,
    write_message,
)
from tests.sync.conftest import MemoryMnemonicStore, MemorySettingsStore

WORDS = ["abandon"] * 23 + ["art"]
OTHER_WORDS = [
    "abandon", "amount", "liar", "amount", "expire", "adjust", "cage", "candy", "arch", "gather",
    "drum", "bullet", "absurd", "math", "era", "live", "bid", "rhythm", "alien", "crouch",
    "range", "attend", "journey", "unaware",
]


def _manager(home: Path) -> SyncManager:
    return SyncManager(home, MemorySettingsStore(SyncSettings(listen_port=0)), MemoryMnemonicStore())


def _seed_master_files(home: Path) -> None:
    (home / "memories").mkdir(parents=True)
    (home / "notes").mkdir()
    (home / "skills").mkdir()
    (home / "SOUL.md").write_text("soul v1", encoding="utf-8")
    (home / "memories" / "MEMORY.md").write_text("memory v1", encoding="utf-8")
    (home / "memories" / "USER.md").write_text("user v1", encoding="utf-8")
    (home / "notes" / "a.md").write_text("note v1", encoding="utf-8")
    (home / "skills" / "s.md").write_text("skill v1", encoding="utf-8")
    (home / "AGENTS.md").write_text("never synced", encoding="utf-8")


@pytest.fixture()
def running_master(tmp_path: Path):
    manager = _manager(tmp_path / "master")
    words = manager.create_chain()
    _seed_master_files(manager.root)
    manager.sync_now()
    server = manager.start_server()
    yield manager, server, words
    manager.stop_server()


class TestChainRoundtrip:
    def test_join_pulls_whitelist_and_pins_fingerprint(self, tmp_path, running_master):
        _master, server, words = running_master
        secondary = _manager(tmp_path / "secondary")
        secondary.join_chain(words, f"127.0.0.1:{server.bound_port}")

        assert (tmp_path / "secondary" / "SOUL.md").read_text(encoding="utf-8") == "soul v1"
        assert (tmp_path / "secondary" / "memories" / "MEMORY.md").read_text(encoding="utf-8") == "memory v1"
        assert (tmp_path / "secondary" / "notes" / "a.md").read_text(encoding="utf-8") == "note v1"
        assert (tmp_path / "secondary" / "skills" / "s.md").read_text(encoding="utf-8") == "skill v1"
        # AGENTS.md is excluded on the master, so it never exists here.
        assert not (tmp_path / "secondary" / "AGENTS.md").exists()

        status = secondary.status()
        assert status.role == ROLE_SECONDARY
        assert status.tracked_files == 5
        assert status.pinned_fingerprint is not None

    def test_local_only_file_survives_join(self, tmp_path, running_master):
        _master, server, words = running_master
        secondary_home = tmp_path / "secondary"
        (secondary_home / "notes").mkdir(parents=True)
        (secondary_home / "notes" / "local.md").write_text("local", encoding="utf-8")

        secondary = _manager(secondary_home)
        secondary.join_chain(words, f"127.0.0.1:{server.bound_port}")
        assert (secondary_home / "notes" / "local.md").read_text(encoding="utf-8") == "local"

    def test_update_archives_then_converges(self, tmp_path, running_master):
        master, server, words = running_master
        secondary = _manager(tmp_path / "secondary")
        secondary.join_chain(words, f"127.0.0.1:{server.bound_port}")

        (master.root / "SOUL.md").write_text("soul v2", encoding="utf-8")
        master.sync_now()
        outcome = secondary.sync_now()
        assert outcome.records == 5  # 5 whitelist files, no tombstones

        assert (tmp_path / "secondary" / "SOUL.md").read_text(encoding="utf-8") == "soul v2"
        versions = tmp_path / "secondary" / ".versions"
        archived = list(versions.iterdir())
        assert len(archived) == 1 and archived[0].name.startswith("SOUL.md.")

        # A second pull carries the same envelopes: nothing new to apply.
        again = secondary.sync_now()
        assert again.records > 0 and again.applied == 0

    def test_tombstone_delete_propagates(self, tmp_path, running_master):
        master, server, words = running_master
        secondary = _manager(tmp_path / "secondary")
        secondary.join_chain(words, f"127.0.0.1:{server.bound_port}")

        (master.root / "notes" / "a.md").unlink()
        master.sync_now()
        secondary.sync_now()

        assert not (tmp_path / "secondary" / "notes" / "a.md").exists()
        archived = list((tmp_path / "secondary" / ".versions").iterdir())
        assert any(name.name.startswith("notes_a.md.") for name in archived)

    def test_two_way_negotiation_pushes_local_newer(self, tmp_path, running_master):
        """v2.8 双向协商:从设备本机新增/修改经 push 进入主设备(主设备留档)。"""
        master, server, words = running_master
        secondary = _manager(tmp_path / "secondary")
        secondary.join_chain(words, f"127.0.0.1:{server.bound_port}")

        # The secondary's own edits: a new file and an update to an existing one.
        (secondary.root / "notes" / "local-note.md").write_text("from secondary", encoding="utf-8")
        (secondary.root / "SOUL.md").write_text("soul from secondary", encoding="utf-8")
        outcome = secondary.sync_now()

        assert outcome.records >= 5  # the master's manifest was exchanged
        assert (master.root / "notes" / "local-note.md").read_text(encoding="utf-8") == "from secondary"
        assert (master.root / "SOUL.md").read_text(encoding="utf-8") == "soul from secondary"
        # The overwritten master copy was archived before the push applied.
        archived = list((master.root / ".versions").iterdir())
        assert any(name.name.startswith("SOUL.md.") for name in archived)

    def test_sessions_whitelist_files_sync(self, tmp_path, running_master):
        """v3.0 聊天会话:meta.json/messages.jsonl 走同步,current 指针不走。"""
        master, server, words = running_master
        secondary = _manager(tmp_path / "secondary")
        secondary.join_chain(words, f"127.0.0.1:{server.bound_port}")

        session_dir = master.root / "sessions" / "0a0a0a0a-1111-2222-3333-444444444444"
        session_dir.mkdir(parents=True)
        (session_dir / "meta.json").write_text('{"id": "s", "title": "t", "createdAt": 1, "updatedAt": 2, "messageCount": 1}', encoding="utf-8")
        (session_dir / "messages.jsonl").write_text('{"role": "user", "content": "hi", "createdAt": 1, "image": null, "images": [], "memoryTrace": null}\n', encoding="utf-8")
        (master.root / "sessions" / "current").write_text("0a0a0a0a-1111-2222-3333-444444444444", encoding="utf-8")
        master.sync_now()
        secondary.sync_now()

        synced = tmp_path / "secondary" / "sessions" / "0a0a0a0a-1111-2222-3333-444444444444"
        assert (synced / "meta.json").is_file()
        assert (synced / "messages.jsonl").is_file()
        assert not (tmp_path / "secondary" / "sessions" / "current").exists()

    def test_secondary_push_does_not_resurrect_master_tombstone_owner(self, tmp_path, running_master):
        """双向语义:从设备较新的墓碑(自己删的镜像文件)push 到主设备,主设备也删。"""
        master, server, words = running_master
        secondary = _manager(tmp_path / "secondary")
        secondary.join_chain(words, f"127.0.0.1:{server.bound_port}")
        master.sync_now()

        (secondary.root / "skills" / "s.md").unlink()
        secondary.sync_now()  # apply(nothing new) + push the tombstone
        master.sync_now()  # fold the pushed tombstone into the master's log

        assert not (master.root / "skills" / "s.md").exists()
        assert not (tmp_path / "secondary" / "skills" / "s.md").exists()

    def test_wrong_mnemonic_is_rejected_and_leaves_nothing(self, tmp_path, running_master):
        _master, server, _words = running_master
        secondary = _manager(tmp_path / "secondary")
        with pytest.raises(SyncError) as excinfo:
            secondary.join_chain(OTHER_WORDS, f"127.0.0.1:{server.bound_port}")
        assert excinfo.value.code == SyncErrorCode.AUTH
        assert not (tmp_path / "secondary" / ".meta" / "state.json").exists()
        assert not (tmp_path / "secondary" / "SOUL.md").exists()
        assert secondary.status().role == ROLE_DISABLED

    def test_invalid_mnemonic_never_connects(self, tmp_path, running_master):
        _master, server, _words = running_master
        secondary = _manager(tmp_path / "secondary")
        with pytest.raises(SyncError) as excinfo:
            secondary.join_chain(["notaword"] * 24, f"127.0.0.1:{server.bound_port}")
        assert excinfo.value.code == SyncErrorCode.NOT_PAIRED

    def test_fingerprint_mismatch_fails_closed(self, tmp_path, running_master):
        _master, server, words = running_master
        secondary = _manager(tmp_path / "secondary")
        secondary.join_chain(words, f"127.0.0.1:{server.bound_port}")

        # Tamper the pin: a different server identity must fail before any record moves.
        secondary._state_store.update(lambda state: replace(state, pinned_fingerprint="ab" * 32))
        with pytest.raises(SyncError) as excinfo:
            secondary.sync_now()
        assert excinfo.value.code == SyncErrorCode.VERIFY

    def test_tofu_pins_on_first_connection(self, tmp_path, running_master):
        master, server, words = running_master
        secondary = _manager(tmp_path / "secondary")
        secondary.join_chain(words, f"127.0.0.1:{server.bound_port}")
        pinned = secondary.status().pinned_fingerprint
        identity = FileSyncIdentity(master.root / ".meta" / "identity")
        assert pinned == identity.fingerprint()

        # The same identity keeps working after the pin is recorded.
        assert secondary.sync_now().records > 0


class TestDiscoveryJoin:
    """v2.8 无地址配对(Android ``joinChain``):候选逐个握手,能通过鉴权的才是同链。"""

    def test_join_without_endpoint_adopts_discovered_master(self, tmp_path, running_master, monkeypatch):
        _master, server, words = running_master
        secondary = _manager(tmp_path / "secondary")
        budgets: list[int] = []

        def discover(timeout_ms: int = 4_000, *, port: int | None = None):
            budgets.append(timeout_ms)
            return [("127.0.0.1", server.bound_port)]

        monkeypatch.setattr(secondary._discovery, "discover", discover)
        secondary.join_chain(words)

        assert budgets == [8_000]  # Android JOIN_DISCOVERY_TIMEOUT_MS
        assert (tmp_path / "secondary" / "SOUL.md").read_text(encoding="utf-8") == "soul v1"
        status = secondary.status()
        assert status.role == ROLE_SECONDARY
        assert status.master_endpoint == f"127.0.0.1:{server.bound_port}"

    def test_join_walks_past_candidates_of_other_chains(self, tmp_path, running_master, monkeypatch):
        """A discovered peer on a different chain fails the handshake; the loop moves on."""
        _master, server, words = running_master
        other = _manager(tmp_path / "other-chain")
        other.create_chain()
        other_server = other.start_server()
        try:
            secondary = _manager(tmp_path / "secondary")
            monkeypatch.setattr(
                secondary._discovery,
                "discover",
                lambda timeout_ms=4_000, port=None: [("127.0.0.1", other_server.bound_port), ("127.0.0.1", server.bound_port)],
            )
            secondary.join_chain(words)

            assert secondary.status().master_endpoint == f"127.0.0.1:{server.bound_port}"
            assert (tmp_path / "secondary" / "SOUL.md").read_text(encoding="utf-8") == "soul v1"
        finally:
            other.stop_server()

    def test_join_without_candidates_leaves_nothing_paired(self, tmp_path, running_master, monkeypatch):
        _master, _server, words = running_master
        secondary = _manager(tmp_path / "secondary")
        monkeypatch.setattr(secondary._discovery, "discover", lambda timeout_ms=4_000, port=None: [])

        with pytest.raises(SyncError) as excinfo:
            secondary.join_chain(words)
        assert excinfo.value.code == SyncErrorCode.UNREACHABLE
        assert not (tmp_path / "secondary" / ".meta" / "state.json").exists()
        assert secondary.status().role == ROLE_DISABLED

    def test_join_pushes_local_files_upstream(self, tmp_path, running_master, monkeypatch):
        """v2.8:加入时本机已有内容一并上行(不是空列表)。"""
        master, server, words = running_master
        secondary_home = tmp_path / "secondary"
        (secondary_home / "notes").mkdir(parents=True)
        (secondary_home / "notes" / "pre-join.md").write_text("written before pairing", encoding="utf-8")

        secondary = _manager(secondary_home)
        monkeypatch.setattr(secondary._discovery, "discover", lambda timeout_ms=4_000, port=None: [("127.0.0.1", server.bound_port)])
        secondary.join_chain(words)

        assert (master.root / "notes" / "pre-join.md").read_text(encoding="utf-8") == "written before pairing"
        # The pulled side still landed locally.
        assert (secondary_home / "SOUL.md").read_text(encoding="utf-8") == "soul v1"


class TestFetchEndpoint:
    def test_fetch_returns_record_for_third_party_clients(self, tmp_path, running_master):
        _master, server, words = running_master
        keys = SyncKeys.from_words(words, SyncMnemonic())

        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        with socket.create_connection(("127.0.0.1", server.bound_port), timeout=15) as raw:
            with context.wrap_socket(raw) as stream:
                nonce_client = new_nonce()
                write_message(stream, SyncMessage(TYPE_HELLO, device_id="probe", nonce_client=nonce_client))
                challenge = read_message(stream)
                assert challenge.type == TYPE_CHALLENGE
                write_message(stream, SyncMessage(TYPE_AUTH, proof=proof(keys.auth_key(), nonce_client, challenge.nonce_server)))
                assert read_message(stream).type == TYPE_AUTH_OK
                write_message(stream, SyncMessage(TYPE_FETCH, record_id="nonexistent"))
                error = read_message(stream)
                assert error.type == "error"
                # A real record id: take it from the listing the server exposes.
                write_message(stream, SyncMessage(TYPE_LIST))
                listing = read_message(stream)
                assert listing.type == TYPE_LIST_RESULT
                target = listing.envelopes[0]
                write_message(stream, SyncMessage(TYPE_FETCH, record_id=target.record_id))
                record = read_message(stream)
                assert record.type == TYPE_RECORD
                assert record.envelopes[0].record_id == target.record_id
                write_message(stream, SyncMessage(TYPE_BYE))


class TestLifecycle:
    def test_reset_chain_discards_pairing_keeps_files(self, tmp_path):
        master = _manager(tmp_path / "master")
        _words = master.create_chain()
        assert master.status().role == ROLE_MASTER
        master.reset_chain()
        assert master.status().role == ROLE_DISABLED
        assert not (tmp_path / "master" / ".meta" / "state.json").exists()

    def test_disconnect_discards_pairing_keeps_files(self, tmp_path, running_master):
        _master, server, words = running_master
        secondary = _manager(tmp_path / "secondary")
        secondary.join_chain(words, f"127.0.0.1:{server.bound_port}")
        (tmp_path / "secondary" / "SOUL.md").write_text("kept", encoding="utf-8")

        secondary.disconnect_and_clear()
        status = secondary.status()
        assert status.role == ROLE_DISABLED and not status.paired
        assert (tmp_path / "secondary" / "SOUL.md").read_text(encoding="utf-8") == "kept"

    def test_pairing_is_refused_while_paired(self, tmp_path, running_master):
        _master, server, words = running_master
        secondary = _manager(tmp_path / "secondary")
        secondary.join_chain(words, f"127.0.0.1:{server.bound_port}")
        with pytest.raises(ValueError, match="already paired"):
            secondary.join_chain(words, f"127.0.0.1:{server.bound_port}")
        with pytest.raises(ValueError, match="already paired"):
            secondary.create_chain()
