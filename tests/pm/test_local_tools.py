"""本机兼容工具跨越安装与 PM 消费边界，重复运行不重复下载。"""
import importlib
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from pm._uv import _toolchain
from pm.local_tools import _compatible, find_local
from pm.lock import Lockfile
from pm.registry import get_package
from pm.store import current_target


@pytest.fixture
def local_toolchain(tmp_path, monkeypatch):
    from pm import paths
    from pm import local_tools

    store = tmp_path / "tools"
    lock = Lockfile(tmp_path / "lock.json")
    target = current_target()
    for name, version in (("python", f"{sys.version_info.major}.{sys.version_info.minor}.99"),
                          ("uv", "0.13.5"), ("node", "26.12.1"), ("npm", "12.0.2")):
        lock.set_pin(name, version, {target: {"url": "https://test.invalid/tool", "sha256": "1" * 64}})
    lock.save()
    monkeypatch.setattr(paths, "lockfile_path", lambda: lock.path)
    monkeypatch.setattr(paths, "store_root", lambda: store)
    monkeypatch.setattr(paths, "writable_store_root", lambda: store)
    monkeypatch.setattr(paths, "facts_path", lambda: store / "facts.json")
    # 决定性地隔离宿主 PATH，但使用真实可执行文件和版本探测。
    binaries = {"python": Path(sys._base_executable)}
    monkeypatch.setattr(local_tools, "_candidates", lambda name, path: iter([binaries[name]]) if name in binaries else iter([]))
    return lock, store, binaries


def _version_tool(directory, name, version):
    directory.mkdir(parents=True, exist_ok=True)
    binary = directory / (name + ".cmd" if os.name == "nt" else name)
    script = f"@echo {version}\r\n" if os.name == "nt" else f"#!/bin/sh\nprintf '%s\\n' '{version}'\n"
    binary.write_text(script, encoding="utf-8")
    binary.chmod(0o755)
    return binary


def test_repeated_ensure_and_pm_use_local_python_and_uv_without_download(local_toolchain, monkeypatch, tmp_path):
    install = importlib.import_module("pm.install")
    _, store, binaries = local_toolchain
    binaries["uv"] = _version_tool(tmp_path / "local uv", "uv", "uv 0.13.5")

    def unexpected_install(*args, **kwargs):
        pytest.fail("compatible local tools must not be installed again")

    monkeypatch.setattr(install, "_install", unexpected_install)
    for _ in range(2):
        install.ensure("uv", explicit=True)
        uv, python = _toolchain(realize=False)
        assert uv == binaries["uv"]
        assert python == binaries["python"]
        result = subprocess.run([str(python), "-I", "-c", "print('local runtime')"],
                                capture_output=True, text=True, check=True, timeout=20)
        assert result.stdout.strip() == "local runtime"
        assert install.installed_package("python").binary == python
        assert str(python.parent) in install.env_for("python")["PATH"].split(os.pathsep)
    assert not store.exists()


@pytest.mark.parametrize("version", [None, "uv 0.12.0"])
def test_missing_or_old_uv_installs_only_missing_tool(local_toolchain, monkeypatch, tmp_path, version):
    install = importlib.import_module("pm.install")
    _, _, binaries = local_toolchain
    if version:
        binaries["uv"] = _version_tool(tmp_path / "old uv", "uv", version)
    installed = []
    monkeypatch.setattr(install, "_install", lambda package, *args, **kwargs: installed.append(package.name))
    install.ensure("uv", explicit=True)
    assert installed == ["uv"]


@pytest.mark.parametrize("name,version,accepted", [
    ("python", "3.14.2", True), ("python", "3.13.9", False), ("python", "3.14.0rc1", False),
    ("node", "v26.0.0", True), ("node", "v24.13.0", False),
    ("npm", "10.9.0", True), ("npm", "11.9.0", True), ("npm", "11.10.0", False),
    ("npm", "11.16.9", False), ("npm", "11.17.0", True),
])
def test_runtime_compatibility(name, version, accepted):
    pins = {"python": "3.14.9+fixture", "node": "26.12.1", "npm": "12.0.2"}
    assert _compatible(name, version, pins[name]) is accepted


def test_node_npm_environment_and_doctor_use_local_selection(local_toolchain, tmp_path, capsys, monkeypatch):
    install = importlib.import_module("pm.install")
    from pm import cli

    _, store, binaries = local_toolchain
    binaries["node"] = _version_tool(tmp_path / "node", "node", "v26.13.0")
    binaries["npm"] = _version_tool(tmp_path / "npm", "npm", "11.17.0")
    monkeypatch.setattr(cli, "_doctor_uv_cache", lambda **kwargs: 0)
    install.ensure("npm", explicit=True)
    env = install.env_for("npm")
    assert env["PATH"].split(os.pathsep)[:2] == [str(binaries["npm"].parent), str(binaries["node"].parent)]
    assert install.installed_package("npm").binary == binaries["npm"]
    assert cli.cmd_doctor(SimpleNamespace(repair=False)) == 0
    output = capsys.readouterr().out
    assert "node: local" in output and "npm: local" in output
    assert not store.exists()


def test_sealed_and_cross_target_lookup_never_reuse_host(local_toolchain):
    lock, store, _ = local_toolchain
    python = get_package("python")
    assert find_local(python, lock, current_target()) is not None
    assert find_local(python, lock, "unrelated-target") is None
    (store.parent / "manifest.json").write_text("{}", encoding="utf-8")
    assert find_local(python, lock, current_target()) is None


@pytest.mark.platforms("windows")
def test_lookup_uses_later_compatible_npm_command_shim(tmp_path, monkeypatch):
    from pm import paths

    monkeypatch.setattr(paths, "store_root", lambda: tmp_path / "store")
    monkeypatch.setattr(paths, "writable_store_root", lambda: tmp_path / "store")
    old = _version_tool(tmp_path / "old", "npm", "11.12.0")
    usable = _version_tool(tmp_path / "usable", "npm", "11.20.0")
    # Node ships both a POSIX shell script and a Windows command shim.
    for binary in (old, usable):
        binary.with_suffix("").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    lock = Lockfile(tmp_path / "lock.json")
    lock.set_pin("npm", "12.0.2", {})
    selected = find_local(get_package("npm"), lock, current_target(),
                          base_env={"PATH": os.pathsep.join([str(old.parent), str(usable.parent)])})
    assert selected is not None and selected.binary == usable
