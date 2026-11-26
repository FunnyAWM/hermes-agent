"""源安装复用本机工具；只探测，不安装、不修改宿主环境。"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from pm import paths
from pm.store import current_target


@dataclass(frozen=True)
class LocalTool:
    path: Path
    version: str
    binary: Path
    env: dict


_COMMANDS = {
    "python": ("python3", "python"),
    "uv": ("uv",), "node": ("node",), "npm": ("npm",),
    "git": ("git",), "ripgrep": ("rg",), "ffmpeg": ("ffmpeg",), "gh": ("gh",),
}
_PYTHON_PROBE = (
    "import json,platform,sys,sysconfig; print(json.dumps({"
    "'version':platform.python_version(),'arch':platform.machine(),"
    "'implementation':sys.implementation.name,"
    "'gil_disabled':bool(sysconfig.get_config_var('Py_GIL_DISABLED')),"
    "'executable':getattr(sys,'_base_executable',sys.executable)}))"
)


def _version(text: str) -> tuple[int, int, int] | None:
    match = re.search(r"(?<!\d)(\d+)\.(\d+)(?:\.(\d+))?", text)
    return tuple(int(part or 0) for part in match.groups()) if match else None


def _compatible(name: str, version: str, pin: str) -> bool:
    actual, required = _version(version), _version(pin)
    if actual is None or required is None or re.search(r"\d(?:a|b|rc)\d|-(?:alpha|beta|rc|dev|pre|nightly)", version):
        return False
    if name == "python":
        return actual[:2] == required[:2]
    if name == "node":
        return actual >= (required[0], 0, 0)
    if name == "npm":
        # 本地 npm 允许复用,但要有主版本下限(至少 npm 10;太老的主版本跑不动
        # 这里依赖的命令);package.json 的 engines 也排除了 11.10–11.16 这个
        # 已知有问题的区间。越界即落回锁定版本。
        return actual >= (10, 0, 0) and not (11, 10, 0) <= actual < (11, 17, 0)
    return actual >= required


def _candidates(name: str, search_path: str):
    try:
        from hermes_platform.resolver import LookupContext, locate_command
        from hermes_platform.resolver.known_dirs import (
            homebrew_dirs, node_tool_dirs, rust_tool_dirs, user_local_bin, windows_user_program_dirs,
        )
    except ModuleNotFoundError as exc:
        # 独立 PM 运行时的闭包只含 pm/，无 hermes_platform；退回受管安装。
        if exc.name != "hermes_platform":
            raise
        return

    commands = list(_COMMANDS[name])
    if name == "python":
        commands.insert(0, getattr(sys, "_base_executable", sys.executable))
    seen = set()
    # 按 PATH 顺序保留后续候选：第一个版本不兼容时仍可复用后面的安装。
    directories = [part for part in search_path.split(os.pathsep) if part]
    directories.extend((*user_local_bin(), *homebrew_dirs(), *windows_user_program_dirs()))
    if name in ("node", "npm"):
        directories.extend(node_tool_dirs())
    if name in ("uv", "ripgrep"):
        directories.extend(rust_tool_dirs())
    for command in commands:
        # 逐目录解析：locate_command 只回每个名字的首个命中，会漏掉 PATH 后面可用的版本。
        for directory in dict.fromkeys(directories):
            result = locate_command(command, LookupContext(path=directory))
            for candidate in result.present:
                binary = Path(candidate.value).absolute()
                if binary not in seen:
                    seen.add(binary)
                    yield binary


@lru_cache(maxsize=128)
def _probe(binary: str, stamp: tuple, args: tuple[str, ...], search_path: str) -> str:
    """缓存只含可执行文件事实，键包含路径、文件状态与子进程 PATH。"""
    try:
        result = subprocess.run(
            [binary, *args], stdin=subprocess.DEVNULL, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=10,
            env={**os.environ, "PATH": search_path}, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _output(binary: Path, args: tuple[str, ...], search_path: str) -> str:
    try:
        stat = binary.stat()
    except OSError:
        return ""
    return _probe(str(binary), (stat.st_mtime_ns, stat.st_size, stat.st_mode), args, search_path)


def _python(binary: Path, search_path: str, target: str) -> tuple[Path, str] | None:
    try:
        info = json.loads(_output(binary, ("-I", "-c", _PYTHON_PROBE), search_path))
    except (ValueError, TypeError):
        return None
    arch = {"amd64": "x64", "x86_64": "x64", "aarch64": "arm64", "arm64": "arm64"}.get(
        str(info.get("arch", "")).lower())
    if info.get("implementation") != "cpython" or info.get("gil_disabled") or arch != target.split("-")[1]:
        return None
    base = Path(info.get("executable", ""))
    return (base, info["version"]) if base.is_file() else None


def _tool(name: str, binary: Path, search_path: str, target: str) -> LocalTool | None:
    if name == "python":
        result = _python(binary, search_path, target)
        if result is None:
            return None
        binary, version = result
    else:
        args = ("-version",) if name == "ffmpeg" else ("--version",)
        version = _output(binary, args, search_path).splitlines()
        version = version[0] if version else ""
    directories = [str(binary.parent)]
    root = binary.parent
    if name == "git" and target.startswith("win32"):
        # Git for Windows 的 bash 属于同一安装，构建也需要它。
        root = binary.parent.parent
        bash = root / "usr" / "bin" / "bash.exe"
        if not _output(bash, ("--version",), search_path):
            return None
        directories.append(str(bash.parent))
    return LocalTool(root, version, binary, {"PATH": directories})


def find_local(package, lockfile, target: str, *, base_env: dict | None = None) -> LocalTool | None:
    """本机源安装优先复用；密封发行包和跨目标 staging 不读取宿主工具。"""
    name = package.name
    if name not in _COMMANDS:
        return None
    if (paths.store_root().parent / "manifest.json").is_file():
        return None
    pin = lockfile.version(name) if lockfile is not None else None
    if not pin or _version(pin) is None:
        return None
    if target != current_target():
        return None
    search_path = (os.environ if base_env is None else base_env).get("PATH", "")
    roots = (paths.store_root().resolve(), paths.writable_store_root().resolve())
    for binary in _candidates(name, search_path):
        # 受管目录仍经 facts 和摘要校验，不能冒充未经管理的本机安装。
        if any(binary.resolve().is_relative_to(root) for root in roots):
            continue
        found = _tool(name, binary, search_path, target)
        if found and _compatible(name, found.version, pin):
            if any(found.binary.resolve().is_relative_to(root) for root in roots):
                continue
            return found
    return None
