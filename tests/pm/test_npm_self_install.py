"""npm's self-install keeps its cache out of the store's download entry.

A cache beside the archive turned the entry's removal into a tree delete
that failed on Windows while Defender held the fresh tarball copy
(``[WinError 145] The directory is not empty: ...fetch-<sha>\\.npm-cache``).
"""

from __future__ import annotations

import subprocess
import io
import tarfile
from pathlib import Path

import pm.install
import pm.packages
from pm.registry import get_package
from pm.store import Store, current_target


def _fake_node(store: Store, target: str) -> None:
    node_bin = get_package("node").binary(store.entry("node-1"), target)
    node_bin.parent.mkdir(parents=True, exist_ok=True)
    node_bin.write_bytes(b"node")


def test_npm_self_install_leaves_download_entry_holding_only_the_archive(tmp_path, monkeypatch):
    target = current_target()
    store = Store(tmp_path / "store")
    _fake_node(store, target)
    node_bin = get_package("node").binary(store.entry("node-1"), target)
    monkeypatch.setattr(pm.install, "_resolved_package",
                        lambda *args: pm.install.InstalledPackage(node_bin.parent, "26.0.0", node_bin))

    caches: list[Path] = []

    def fake_npm(cmd, **kwargs):
        assert Path(cmd[0]).samefile(node_bin)
        assert Path(cmd[1]).read_bytes() == b"fixture npm"
        # Stand in for npm: cacache writes the installed tarball into the cache.
        cache = Path(kwargs["env"]["npm_config_cache"])
        content = cache / "_cacache" / "content-v2" / "sha512" / "b8" / "85"
        content.mkdir(parents=True)
        (content / "blob").write_bytes(b"tarball")
        caches.append(cache)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(pm.packages.subprocess, "run", fake_npm)

    entry = store.entry("fetch-" + "a" * 64)
    entry.mkdir(parents=True)
    archive = entry / "npm-1.0.0.tgz"
    with tarfile.open(archive, "w:gz") as tar:
        member = tarfile.TarInfo("package/bin/npm-cli.js")
        member.size = len(b"fixture npm")
        tar.addfile(member, io.BytesIO(b"fixture npm"))
    staged = tmp_path / "scratch" / "tree"

    get_package("npm").unpack(archive, staged, target)

    assert [p.name for p in entry.iterdir()] == [archive.name]
    assert caches and not caches[0].is_relative_to(staged)
    assert not caches[0].exists()
