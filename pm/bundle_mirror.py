"""Aliyun download locations for frozen desktop build snapshots."""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import os
from pathlib import Path

_ALIYUN = "http://mirrors.aliyun.com/pypi"
_PYPI_FILES = b"https://files.pythonhosted.org/packages/"
# wheel is a PEP 517 build requirement outside the runtime lock.
_WHEEL = ("wheel @ " + _ALIYUN + "/packages/2e/29/69cfbb602cd91690c55d38ba9fe53e6a7e76a6fa647bf38f19c138d25449/"
          "wheel-0.48.0-py3-none-any.whl#sha256=3217dcc807155e45db462d7ef2431f5ddda0d7273b700d05a67b271ceb1287ab")


def _build_wheels(mirrored_lock: bytes) -> list[str]:
    import tomllib

    # The mirror lacks upload times. Use known wheels and hashes for isolated builds.
    packages = {p["name"]: p for p in tomllib.loads(mirrored_lock.decode())["package"]}
    pins = [_WHEEL]
    for name in ("setuptools", "packaging"):
        wheel, = packages[name]["wheels"]
        pins.append(f"{name} @ {wheel['url']}#{wheel['hash'].replace(':', '=', 1)}")
    return pins


@contextmanager
def mirrored_build_inputs(project: Path):
    """Redirect PyPI files during sync and restore the snapshot even on failure."""
    index = os.environ.get("UV_DEFAULT_INDEX", "").rstrip("/")
    if not os.environ.get("HERMES_BUNDLE_TOOLS") or index != _ALIYUN + "/simple":
        yield
        return

    import tomllib

    lock = project / "uv.lock"
    manifest = project / "pyproject.toml"
    with ExitStack() as restore:
        # Frozen sync follows the lock's absolute artifact URLs, independently of the index.
        original = lock.read_bytes()
        mirrored = original.replace(_PYPI_FILES, (_ALIYUN + "/packages/").encode())
        restore.callback(lock.write_bytes, original)
        lock.write_bytes(mirrored)
        metadata = manifest.read_bytes()
        project_config = tomllib.loads(metadata.decode())
        if "build-system" in project_config:
            import tomli_w

            # Frozen sync reads build constraints from the lock, but reads PEP 517 requirements here.
            project_config["build-system"]["requires"].extend(_build_wheels(mirrored))
            project_config["tool"]["uv"]["require-build-hashes"] = True
            restore.callback(manifest.write_bytes, metadata)
            manifest.write_bytes(tomli_w.dumps(project_config).encode())
        yield
