"""Whitelist and engine semantics tests, mirroring the Android ``SyncScopeTest`` and
``SyncChannelTest`` contracts on the PC side.

The whitelist cases are a cross-client contract: the Android client's
``MemoryRepository.isSyncablePath`` is the sole source of truth, and these cases must
change only together with it (the memory-contract fixture guards the same parity).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from sync import scope
from sync.clock import SyncClock
from sync.crypto import sha256_hex
from sync.engine import SyncEngine, apply_sync_file, archive_and_delete, read_current
from sync.errors import SyncErrorCode
from sync.keys import SyncKeys
from sync.mnemonic import SyncMnemonic
from sync.records import SyncPayload, pack, unpack
from sync.state_store import SyncStateStore

WORDS = ["abandon"] * 23 + ["art"]


@pytest.fixture(scope="module")
def keys() -> SyncKeys:
    return SyncKeys.from_words(WORDS, SyncMnemonic())


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    return tmp_path / "home"


@pytest.fixture()
def store(root: Path) -> SyncStateStore:
    store = SyncStateStore(root / ".meta")
    assert store.create() is not None
    return store


def _engine(root: Path, store: SyncStateStore, keys: SyncKeys) -> SyncEngine:
    return SyncEngine(root, store, SyncMnemonic(), lambda: WORDS)


# ── whitelist ────────────────────────────────────────────────────────────────────


class TestScope:
    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("SOUL.md", True),
            ("memories/MEMORY.md", True),
            ("memories/USER.md", True),
            ("notes/a.md", True),
            ("notes/sub/dir/deep.md", True),
            ("skills/skill.md", True),
            ("skills/Sub/SKILL.MD", True),  # .md matches case-insensitively
            ("agents.md", False),  # AGENTS.md excluded regardless of case
            ("AGENTS.md", False),
            ("memories/OTHER.md", False),  # only the two memory files sync
            ("notes.md", False),  # a top-level file outside the whitelist
            (".meta/state.json", False),
            (".versions/SOUL.md.123", False),
            ("notes/a.txt", False),
            ("notes/a", False),
            ("", False),
            ("/abs/x.md", False),
            ("../escape.md", False),
            ("notes/../x.md", False),
            (".memory_incomplete", False),
        ],
    )
    def test_is_syncable_path(self, path: str, expected: bool):
        assert scope.is_syncable_path(path) is expected

    def test_scan_returns_sorted_whitelist_only(self, root: Path):
        (root / "memories").mkdir(parents=True)
        (root / "notes" / "sub").mkdir(parents=True)
        (root / "skills").mkdir()
        (root / "SOUL.md").write_text("soul", encoding="utf-8")
        (root / "memories" / "MEMORY.md").write_text("mem", encoding="utf-8")
        (root / "AGENTS.md").write_text("agents", encoding="utf-8")
        (root / "notes" / "b.md").write_text("b", encoding="utf-8")
        (root / "notes" / "sub" / "a.md").write_text("a", encoding="utf-8")
        (root / "notes" / "junk.txt").write_text("junk", encoding="utf-8")
        (root / "skills" / "skill.md").write_text("s", encoding="utf-8")
        (root / ".meta").mkdir()
        (root / ".meta" / "state.json").write_text("{}", encoding="utf-8")

        scanned = scope.scan(root)
        assert [entry.relative_path for entry in scanned] == [
            "SOUL.md",
            "memories/MEMORY.md",
            "notes/b.md",
            "notes/sub/a.md",
            "skills/skill.md",
        ]
        assert all(scope.is_syncable_path(entry.relative_path) for entry in scanned)

    def test_scan_never_leaves_the_root(self, root: Path):
        outside = root.parent / "outside.md"
        outside.write_text("out", encoding="utf-8")
        (root / "notes").mkdir(parents=True)
        (root / "notes" / "link.md").write_text("x", encoding="utf-8")
        assert all(
            (root / entry.relative_path).resolve() == entry.file.resolve() for entry in scope.scan(root)
        )
        assert outside.read_text(encoding="utf-8") == "out"


# ── engine apply semantics ───────────────────────────────────────────────────────


class TestEngineApply:
    def test_convergence_and_tombstone_archival(self, root, store, keys):
        engine = _engine(root, store, keys)
        hlc = SyncClock(100, 0, "master")
        create = pack(keys, hlc, "master", SyncPayload("notes/a.md", "v1"))
        assert apply_remote_ok(engine, [create]) is None
        assert (root / "notes/a.md").read_text(encoding="utf-8") == "v1"

        # Update under a newer clock: the old content lands in .versions/.
        update = pack(keys, hlc.tick(101), "master", SyncPayload("notes/a.md", "v2"))
        assert apply_remote_ok(engine, [update]) is None
        assert (root / "notes/a.md").read_text(encoding="utf-8") == "v2"
        versions = root / ".versions"
        archives = list(versions.iterdir())
        assert len(archives) == 1 and archives[0].name.startswith("notes_a.md.")

        # Delete under a newer clock: archive-then-delete, entry dropped.
        delete = pack(keys, hlc.tick(101).tick(102), "master", SyncPayload("notes/a.md", deleted=True))
        assert apply_remote_ok(engine, [delete]) is None
        assert not (root / "notes/a.md").exists()
        assert len(list(versions.iterdir())) == 2

    def test_stale_envelopes_are_skipped(self, root, store, keys):
        engine = _engine(root, store, keys)
        fresh = pack(keys, SyncClock(200, 0, "master"), "master", SyncPayload("notes/a.md", "fresh"))
        stale = pack(keys, SyncClock(100, 0, "master"), "master", SyncPayload("notes/a.md", "stale"))
        assert apply_remote_ok(engine, [fresh, stale]) is None
        assert (root / "notes/a.md").read_text(encoding="utf-8") == "fresh"

        state = store.load()
        # A same-or-older envelope must not bump applied_count on a later pull.
        assert apply_remote_ok(engine, [stale]) is None
        assert store.load().applied_count == state.applied_count

    def test_local_only_file_is_never_touched(self, root, store, keys):
        engine = _engine(root, store, keys)
        (root / "notes").mkdir(parents=True)
        (root / "notes" / "local.md").write_text("local", encoding="utf-8")
        other = pack(keys, SyncClock(100, 0, "master"), "master", SyncPayload("notes/other.md", "other"))
        assert apply_remote_ok(engine, [other]) is None
        assert (root / "notes/local.md").read_text(encoding="utf-8") == "local"
        assert not (root / ".versions").exists()

    def test_local_only_file_survives_same_path_tombstone(self, root, store, keys):
        # A tombstone for a path this device never mirrored deletes nothing.
        engine = _engine(root, store, keys)
        (root / "notes").mkdir(parents=True)
        (root / "notes" / "local.md").write_text("local", encoding="utf-8")
        tombstone = pack(keys, SyncClock(300, 0, "master"), "master", SyncPayload("notes/local.md", deleted=True))
        assert apply_remote_ok(engine, [tombstone]) is None
        assert (root / "notes/local.md").exists()

    def test_whole_batch_rejected_on_one_bad_envelope(self, root, store, keys):
        engine = _engine(root, store, keys)
        good = pack(keys, SyncClock(100, 0, "master"), "master", SyncPayload("notes/good.md", "good"))
        bad = replace(
            pack(keys, SyncClock(101, 0, "master"), "master", SyncPayload("notes/bad.md", "bad")),
            digest="0" * 64,
        )
        applied = apply_remote_ok(engine, [good, bad])
        assert applied == SyncErrorCode.VERIFY
        assert not (root / "notes/good.md").exists()
        assert not (root / "notes/bad.md").exists()
        assert store.load().applied_count == 0

    def test_digest_match_skips_rewrite(self, root, store, keys):
        engine = _engine(root, store, keys)
        (root / "notes").mkdir(parents=True)
        (root / "notes" / "a.md").write_text("same", encoding="utf-8")
        envelope = pack(keys, SyncClock(100, 0, "master"), "master", SyncPayload("notes/a.md", "same"))
        assert apply_remote_ok(engine, [envelope]) is None
        # Content already matches: no archive, only the cache entry is recorded.
        assert not (root / ".versions").exists()
        state = store.load()
        assert state.entries[0].relative_path == "notes/a.md"

    def test_tombstone_for_path_with_no_entry_deletes_nothing(self, root, store, keys):
        engine = _engine(root, store, keys)
        tombstone = pack(keys, SyncClock(100, 0, "master"), "master", SyncPayload("memories/USER.md", deleted=True))
        assert apply_remote_ok(engine, [tombstone]) is None
        assert not (root / "memories/USER.md").exists()  # never existed, stays absent
        assert store.load().entries == []


# ── master record-log maintenance ────────────────────────────────────────────────


class TestMasterRefresh:
    def test_add_modify_delete_resurrect(self, root, store, keys):
        engine = _engine(root, store, keys)
        (root / "notes").mkdir(parents=True)
        (root / "notes" / "a.md").write_text("v1", encoding="utf-8")

        assert engine.refresh_from_local_changes(now_ms=1000)
        state = store.load()
        assert [entry.relative_path for entry in state.entries] == ["notes/a.md"]
        assert state.tombstones == []

        (root / "notes" / "a.md").write_text("v2", encoding="utf-8")
        assert engine.refresh_from_local_changes(now_ms=2000)
        state = store.load()
        assert state.entries[0].digest == sha256_hex(b"v2")

        (root / "notes" / "a.md").unlink()
        assert engine.refresh_from_local_changes(now_ms=3000)
        state = store.load()
        assert state.entries == []
        assert [t.relative_path for t in state.tombstones] == ["notes/a.md"]

        # Resurrection cleans the tombstone and re-records the entry.
        (root / "notes" / "a.md").write_text("back", encoding="utf-8")
        assert engine.refresh_from_local_changes(now_ms=4000)
        state = store.load()
        assert [entry.relative_path for entry in state.entries] == ["notes/a.md"]
        assert state.tombstones == []

    def test_tombstone_removal_only_tracks_whitelisted_paths(self, root, store, keys):
        engine = _engine(root, store, keys)
        (root / "AGENTS.md").write_text("agents", encoding="utf-8")
        (root / "notes").mkdir()
        (root / "notes" / "a.md").write_text("a", encoding="utf-8")
        assert engine.refresh_from_local_changes(now_ms=1000)
        paths = [entry.relative_path for entry in store.load().entries]
        assert paths == ["notes/a.md"]

    def test_refresh_without_keys_is_noop(self, root, store):
        engine = SyncEngine(root, store, SyncMnemonic(), lambda: None)
        assert not engine.refresh_from_local_changes()

    def test_apply_without_state_is_state_corrupt(self, root, tmp_path, keys):
        # No .meta/state.json: applying must fail closed, not treat as unpaired-and-apply.
        engine = _engine(tmp_path / "fresh", SyncStateStore(tmp_path / "fresh" / ".meta"), keys)
        envelope = pack(keys, SyncClock(1, 0, "m"), "m", SyncPayload("notes/a.md", "x"))
        assert engine.apply_remote([envelope], keys) == SyncErrorCode.STATE_CORRUPT


# ── helpers ──────────────────────────────────────────────────────────────────────


def apply_remote_ok(engine: SyncEngine, envelopes) -> SyncErrorCode | None:
    return engine.apply_remote(envelopes, SyncKeys.from_words(WORDS, SyncMnemonic()))
