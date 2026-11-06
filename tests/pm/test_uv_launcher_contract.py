"""MCP 的 uv/uvx 必须是受管副本；本机复用只服务 PM 自己的工具链。"""
import os
from pathlib import Path

import pytest

from pm import paths
from pm.install import _resolved_package, uv_launcher
from pm.local_tools import LocalTool
from pm.lock import Facts, Lockfile
from pm.registry import get_package
from pm.store import Store, current_target

SHA = "a" * 64


@pytest.fixture
def managed_uv(tmp_path, monkeypatch):
    store = tmp_path / "tools"
    lock = Lockfile(tmp_path / "lock.json")
    target = current_target()
    monkeypatch.setattr(paths, "store_root", lambda: store)
    monkeypatch.setattr(paths, "writable_store_root", lambda: store)
    monkeypatch.setattr(paths, "facts_path", lambda: store / "facts.json")
    monkeypatch.setattr(paths, "lockfile_path", lambda: lock.path)
    lock.set_pin("uv", "0.13.5", {target: {"url": "https://test.invalid/uv", "sha256": SHA}})
    lock.save()
    entry_name = f"uv-0.13.5-{target}"
    entry = Store(store).entry(entry_name)
    binary = entry / ("uv.exe" if os.name == "nt" else "uv")
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(0o755)
    Facts(paths.facts_path()).record("uv", "0.13.5", entry_name, {"PATH": [str(entry)]},
                                     store, target=target, artifacts=[SHA])
    return lock, binary


def _local_uv(tmp_path, monkeypatch, binary: Path) -> LocalTool:
    local = tmp_path / "host" / binary.name
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    monkeypatch.setattr("pm.install.find_local", lambda *args, **kwargs:
                        LocalTool(local.parent, "0.13.5", local, {"PATH": [str(local.parent)]}))
    return local


def test_launcher_ignores_a_compatible_host_uv(managed_uv, tmp_path, monkeypatch):
    _, binary = managed_uv
    local = _local_uv(tmp_path, monkeypatch, binary)
    launched = uv_launcher("uv")
    assert launched is not None and launched.samefile(binary)
    assert launched != local


def test_selected_package_still_prefers_the_host_uv(managed_uv, tmp_path, monkeypatch):
    lock, binary = managed_uv
    local = _local_uv(tmp_path, monkeypatch, binary)
    selected = _resolved_package(get_package("uv"), lock, current_target())
    assert selected.binary == local
