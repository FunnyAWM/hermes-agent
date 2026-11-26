#!/usr/bin/env python3
"""Capture default-config egress for T-11 (desktop, PRD §8.1 隐 02).

Three modes, all read-only with respect to user state:

* ``--child`` (internal): install a ``sys.addaudithook`` recorder, then run ``serve`` headless on
  127.0.0.1 until the timer exits. Records every ``socket.connect`` / ``socket.getaddrinfo`` target.
* default: spawn the child with an isolated ``HERMES_HOME``, sample its TCP/UDP sockets with psutil
  for ``--duration`` seconds, merge audit hosts with NetLog hosts (if given) and classify them.
* ``--netlog <file>``: parse an Electron ``--log-net-log`` file (``IncludeSensitive``) without
  spawning anything.

Loopback targets are exempt; the model endpoint passed with ``--allow`` is expected; any other
non-loopback host is a violation. Passive update hosts are violations on sight.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PASSIVE_UPDATE_DOMAINS = (
    "github.com",
    "api.github.com",
    "raw.githubusercontent.com",
    "objects.githubusercontent.com",
    "codeload.github.com",
    "nousresearch.com",
)
_LOOPBACK = (ipaddress.ip_network("127.0.0.0/8"), ipaddress.ip_network("::1/128"))
_NETWORK_EVENT_PREFIXES = (
    "HOST_RESOLVER",
    "QUIC_SESSION",
    "TRANSPORT_CONNECT_JOB",
    "SOCKET_CONNECT",
    "URL_REQUEST",
    "TCP_CONNECT",
    "UDP_CONNECT",
    "DNS_",
)


def is_loopback_host(host: str) -> bool:
    value = (host or "").strip().strip("[]").lower()
    if value in {"", "localhost"}:
        return True
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return any(address in network for network in _LOOPBACK)


def _host_from_url(value: str) -> str | None:
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError:
        return None
    return parsed.hostname if parsed.scheme else None


def _host_from_address(value: str) -> str | None:
    value = value.strip()
    if value.startswith("["):
        end = value.find("]")
        return value[1:end] if end > 0 else None
    if ":" in value:
        return value.rsplit(":", 1)[0] or None
    return None


def hosts_from_netlog(payload: dict) -> set[str]:
    """All hostnames a Chromium NetLog mentions on network events.

    Real NetLog files encode event types as numeric ids and ship a
    ``constants.logEventTypes`` name→id table; synthetic fixtures and some
    tools use the names directly. Resolve both, or a real capture silently
    parses to zero hosts and the run looks clean.
    """
    hosts: set[str] = set()
    raw_types = (payload.get("constants") or {}).get("logEventTypes") or {}
    type_names = (
        {value: name for name, value in raw_types.items()}
        if isinstance(raw_types, dict)
        else {}
    )

    def event_name(event: dict) -> str:
        value = event.get("type")
        if isinstance(value, str):
            return value
        if isinstance(value, int):
            return str(type_names.get(value, ""))
        return ""

    def add(value) -> None:
        if not isinstance(value, str) or not value.strip():
            return
        value = value.strip()
        for candidate in (_host_from_url(value), _host_from_address(value)):
            if candidate:
                hosts.add(candidate.lower())
                return
        if " " not in value and "/" not in value:
            hosts.add(value.lower())

    def walk(node) -> None:
        if isinstance(node, dict):
            for key in (
                "host",
                "hostname",
                "url",
                "address",
                "server_address",
                "ip_endpoint",
            ):
                if key in node:
                    add(node[key])
            for value in node.values():
                if isinstance(value, (dict, list)):
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for event in payload.get("events", []) or []:
        if not isinstance(event, dict):
            continue
        if event_name(event).startswith(_NETWORK_EVENT_PREFIXES):
            walk(event.get("params") or {})
            if event.get("source"):
                walk(event["source"])
    return hosts


def hosts_from_audit(lines: list[str]) -> set[str]:
    hosts: set[str] = set()
    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        host = record.get("host")
        if isinstance(host, str) and host.strip():
            hosts.add(host.strip().lower())
    return hosts


def classify(hosts: set[str], allowed_hosts: set[str] | None = None) -> dict:
    allowed_hosts = {host.lower() for host in (allowed_hosts or set())}
    loopback, allowed, violations = [], [], []
    for host in sorted(hosts):
        if is_loopback_host(host):
            loopback.append(host)
        elif any(
            host == domain or host.endswith("." + domain)
            for domain in PASSIVE_UPDATE_DOMAINS
        ):
            violations.append({"host": host, "reason": "passive-update domain"})
        elif host in allowed_hosts:
            allowed.append(host)
        else:
            violations.append(
                {"host": host, "reason": "unexpected non-loopback target"}
            )
    return {"loopback": loopback, "allowed": allowed, "violations": violations}


def _dedupe_violations(items: list[dict]) -> list[dict]:
    seen: set[tuple[str, str]] = set()
    unique: list[dict] = []
    for item in items:
        key = (str(item.get("host")), str(item.get("reason")))
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def _child(audit_path: Path, duration: float) -> int:
    import threading

    def record(event: str, args: tuple) -> None:
        if event == "socket.connect" and len(args) >= 2:
            address = args[1]
            if isinstance(address, tuple) and address:
                payload = {"event": event, "host": str(address[0]), "port": address[1]}
            else:
                return
        elif event == "socket.getaddrinfo" and args:
            payload = {"event": event, "host": str(args[0])}
        else:
            return
        with audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload) + "\n")

    from hermes_cli.web_server import start_server

    sys.addaudithook(record)
    threading.Timer(duration, lambda: os._exit(0)).start()
    start_server(host="127.0.0.1", port=0, open_browser=False, headless=True)
    return 0


def _sample_connections(pid: int, duration: float) -> list[str]:
    try:
        import psutil
    except ImportError:  # pragma: no cover - psutil ships with the PC runtime
        return []
    endpoints: set[str] = set()
    process = psutil.Process(pid)
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        try:
            processes = [process, *process.children(recursive=True)]
        except psutil.Error:
            break
        for child in processes:
            for kind in ("tcp", "udp"):
                try:
                    connections = child.net_connections(kind=kind)
                except (psutil.Error, OSError):
                    continue
                for conn in connections:
                    if conn.raddr:
                        endpoints.add(f"{conn.raddr.ip}:{conn.raddr.port}")
        time.sleep(0.25)
    return sorted(endpoints)


def _mktemp_path(prefix: str, suffix: str) -> Path:
    descriptor, name = tempfile.mkstemp(prefix=prefix, suffix=suffix)
    os.close(descriptor)
    return Path(name)


def run_python_probe(duration: float, allowed_hosts: set[str], home: Path) -> dict:
    audit_path = _mktemp_path("hermes-t11-audit-", ".jsonl")
    env = dict(os.environ)
    env.update(HERMES_HOME=str(home), HERMES_SERVE_HEADLESS="1", PYTHONUNBUFFERED="1")
    for key in (
        "HERMES_DESKTOP",
        "HERMES_PARENT_PID",
        "HERMES_PARENT_START_MARKER",
        "HERMES_PARENT_NONCE",
    ):
        env.pop(key, None)
    proc = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--child",
            "--audit-json",
            str(audit_path),
            "--duration",
            str(duration + 1),
        ],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        endpoints = _sample_connections(proc.pid, duration)
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=30)
    lines = (
        audit_path.read_text(encoding="utf-8-sig").splitlines()
        if audit_path.exists()
        else []
    )
    audit_path.unlink(missing_ok=True)
    hosts = hosts_from_audit(lines)
    report = classify(hosts, allowed_hosts)
    report["remote_endpoints"] = endpoints
    report["unattributed_non_loopback"] = [
        endpoint
        for endpoint in endpoints
        if not is_loopback_host(_host_from_address(endpoint) or "")
    ]
    report["ok"] = not report["violations"]
    return report


def netlog_report(path: Path, allowed_hosts: set[str]) -> dict:
    """Classify the hosts in a Chromium NetLog file.

    A missing or empty file is a capture failure, not a clean result: the
    caller must be able to tell "the app made no outbound requests" from
    "Electron never wrote the log".
    """
    if not path.exists() or path.stat().st_size == 0:
        return {
            "ok": False,
            "blocked_reason": f"netlog not written: {path}",
            "hosts": [],
            "loopback": [],
            "allowed": [],
            "violations": [],
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig", errors="replace"))
    except (OSError, ValueError) as exc:
        return {
            "ok": False,
            "blocked_reason": f"netlog unreadable: {exc}",
            "hosts": [],
            "loopback": [],
            "allowed": [],
            "violations": [],
        }
    hosts = hosts_from_netlog(payload)
    report = classify(hosts, allowed_hosts)
    return {"ok": not report["violations"], "hosts": sorted(hosts), **report}


def _stop_process(proc: subprocess.Popen, timeout: float = 15.0) -> None:
    """Close Electron gracefully so Chromium finalizes the NetLog JSON.

    ``proc.kill`` maps to TerminateProcess on Windows and truncates the log
    mid-JSON. A bare ``taskkill /PID`` posts WM_CLOSE, which lets Electron run
    its normal shutdown and flush the closing events.
    """
    if proc.poll() is None:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=30,
                check=False,
            )
        else:
            proc.terminate()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=30)


def capture_desktop(
    exe: Path,
    duration: float,
    home: Path,
    netlog: Path,
    allowed_hosts: set[str] | None = None,
) -> dict:
    if not exe.exists():
        return {
            "ok": False,
            "blocked_reason": f"packaged Electron app not found: {exe}",
            "violations": [],
            "netlog": None,
        }
    # Chromium needs these roots to exist before the app writes its profile;
    # seed them inside the isolated home so the run cannot touch the real one.
    for sub in ("LocalAppData", "AppData"):
        (home / sub).mkdir(parents=True, exist_ok=True)
    # --log-net-log truncates an existing file; remove mkstemp's empty file so
    # a stale empty log can never be mistaken for a successful capture.
    netlog.unlink(missing_ok=True)
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"HERMES_DESKTOP", "HERMES_PARENT_PID"}
    }
    # Do NOT redirect USERPROFILE/HOME: the desktop bootstrap exits before
    # Electron initializes when they point somewhere new. Chromium's profile
    # lives under APPDATA/LOCALAPPDATA and app data under HERMES_HOME, so those
    # two are enough to keep the run isolated from the real user state.
    env.update(
        HERMES_HOME=str(home),
        LOCALAPPDATA=str(home / "LocalAppData"),
        APPDATA=str(home / "AppData"),
    )
    proc = subprocess.Popen(
        [
            str(exe),
            f"--log-net-log={netlog}",
            "--net-log-capture-mode=IncludeSensitive",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        endpoints = _sample_connections(proc.pid, duration)
    finally:
        _stop_process(proc)
    report = netlog_report(netlog, allowed_hosts or set())
    return {"netlog": str(netlog), "remote_endpoints": endpoints, **report}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--audit-json", type=Path, default=Path("audit.jsonl"), help=argparse.SUPPRESS
    )
    parser.add_argument("--duration", type=float, default=10.0)
    parser.add_argument(
        "--allow", action="append", default=[], help="expected model endpoint host"
    )
    parser.add_argument(
        "--netlog", type=Path, help="parse an existing Electron NetLog file"
    )
    parser.add_argument(
        "--desktop-exe",
        type=Path,
        default=REPO_ROOT / "release" / "win-unpacked" / "Hermes.exe",
    )
    parser.add_argument("--skip-electron", action="store_true")
    parser.add_argument("--json", type=Path, help="write the report here")
    args = parser.parse_args(argv)

    if args.child:
        return _child(args.audit_json, args.duration)

    report: dict = {"mode": "python-probe", "duration": args.duration}
    with tempfile.TemporaryDirectory(prefix="hermes-t11-home-") as tmp:
        home = Path(tmp)
        allowed = {host.lower() for host in args.allow}
        report.update(run_python_probe(args.duration, allowed, home))
        if args.netlog:
            payload = json.loads(args.netlog.read_text(encoding="utf-8-sig"))
            net_report = classify(hosts_from_netlog(payload), allowed)
            report["netlog"] = {"path": str(args.netlog), **net_report}
            report["violations"].extend(net_report["violations"])
            report["ok"] = report["ok"] and not net_report["violations"]
        elif not args.skip_electron:
            desktop = capture_desktop(
                args.desktop_exe,
                args.duration,
                home,
                _mktemp_path("hermes-t11-netlog-", ".json"),
                allowed,
            )
            report["electron"] = desktop
            report["violations"].extend(desktop.get("violations", []))
            if desktop.get("blocked_reason"):
                report["blocked_reason"] = desktop["blocked_reason"]
            report["ok"] = report["ok"] and bool(desktop.get("ok"))

    report["violations"] = _dedupe_violations(report["violations"])
    if args.json:
        args.json.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
