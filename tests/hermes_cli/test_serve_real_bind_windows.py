"""T-09b on Windows: the real bind is loopback-only by default and fail-closed off-loopback.

A real ``start_server`` subprocess on Windows: the default 127.0.0.1 bind answers ``/api/status``
and is not reachable on any LAN address; a 0.0.0.0 bind without an auth provider refuses to start
instead of serving an unauthenticated dashboard on the LAN.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path

import pytest

from hermes_cli.lan_addresses import enumerate_lan_urls

REPO_ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.platforms("windows")


def _spawn(host: str, tmp_path: Path) -> subprocess.Popen:
    home = tmp_path / "hermes_home"
    home.mkdir(exist_ok=True)
    env = dict(os.environ)
    env.update(HERMES_HOME=str(home), HERMES_SERVE_HEADLESS="1", PYTHONUNBUFFERED="1")
    for key in ("HERMES_DESKTOP", "HERMES_PARENT_PID", "HERMES_PARENT_START_MARKER",
                "HERMES_PARENT_NONCE", "HERMES_DASHBOARD_SESSION_TOKEN",
                "HERMES_DASHBOARD_PUBLIC_URL"):
        env.pop(key, None)
    code = (
        "from hermes_cli.web_server import start_server\n"
        f"start_server(host={host!r}, port=0, open_browser=False, headless=True)\n"
    )
    return subprocess.Popen([sys.executable, "-c", code], cwd=str(REPO_ROOT), env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def _read_ready_port(proc: subprocess.Popen, timeout: float = 120.0) -> int | None:
    lines: list[str] = []
    found = threading.Event()

    def pump() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.append(line)
            if "HERMES_BACKEND_READY" in line:
                found.set()
                return

    threading.Thread(target=pump, daemon=True).start()
    found.wait(timeout)
    for line in lines:
        if "HERMES_BACKEND_READY" in line:
            return int(line.strip().rsplit("port=", 1)[1])
    return None


def _stop(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()


def test_default_bind_is_loopback_only(tmp_path):
    proc = _spawn("127.0.0.1", tmp_path)
    try:
        port = _read_ready_port(proc)
        assert port, "loopback backend never announced its port"
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=10) as response:
            assert response.status == 200
        for url in enumerate_lan_urls(port):
            host = url.split("//", 1)[1].rsplit(":", 1)[0]
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.settimeout(1.0)
                with pytest.raises(OSError):
                    probe.connect((host, port))
    finally:
        _stop(proc)


def test_non_loopback_bind_without_auth_provider_refuses_to_start(tmp_path):
    proc = _spawn("0.0.0.0", tmp_path)
    try:
        out, _ = proc.communicate(timeout=180)
    except subprocess.TimeoutExpired:
        _stop(proc)
        pytest.fail("a 0.0.0.0 bind without an auth provider kept serving")
    assert proc.returncode != 0, out
    assert "auth gate engages on non-loopback binds" in out, out
    assert "HERMES_BACKEND_READY" not in out
