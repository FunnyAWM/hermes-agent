"""Observe passive update entry points on Windows in disposable profiles.

This checks Python connection attempts and samples the real backend's TCP
connections with netstat. It does not certify Electron, model traffic or UDP.
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import http.client
import ipaddress
import json
import math
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run_child(args: argparse.Namespace) -> None:
    with args.audit.open("w", encoding="utf-8") as audit:
        def record(event, values):
            if event == "socket.connect":
                audit.write(json.dumps({"event": event, "address": values[1]}) + "\n")
                audit.flush()

        sys.addaudithook(record)
        if args.child == "probe":
            from hermes_cli.source_check import main

            sys.argv = ["source_check", "--install-root", str(ROOT), "--home", str(args.home)]
            main()
        else:
            from hermes_cli.web_server import start_server

            start_server(port=args.port, headless=True, isolated=True, open_browser=False)


def tcp_rows(process: subprocess.Popen[bytes], netstat: str) -> list[str]:
    import psutil

    pids = {process.pid, *(p.pid for p in psutil.Process(process.pid).children(recursive=True))}
    output = subprocess.run([netstat, "-ano", "-p", "tcp"], capture_output=True,
                            text=True, check=True, timeout=10).stdout
    return [line.strip() for line in output.splitlines()
            if line.strip().startswith("TCP ") and int(line.split()[-1]) in pids]


def poll_backend(process: subprocess.Popen[bytes], netstat: str, port: int,
                 token: str, seconds: float) -> dict[str, object]:
    rows: set[str] = set()
    ready_at = None
    statuses = []
    deadline = time.monotonic() + 60
    while ready_at is not None or time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Probe backend exited before observation completed.")
        rows.update(tcp_rows(process, netstat))
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        try:
            connection.request("GET", "/api/hermes/update/check", headers={"Authorization": f"Bearer {token}"})
            response = connection.getresponse()
            if response.status != 200:
                raise RuntimeError(f"Update endpoint returned HTTP {response.status}.")
            status = json.loads(response.read())
            if status.get("reason") != "disabled" or status.get("behind") is not None:
                raise RuntimeError("A passive backend check was not disabled.")
            statuses.append({key: status.get(key) for key in ("reason", "behind", "update_available")})
            if ready_at is None:
                ready_at = time.monotonic()
            if time.monotonic() - ready_at >= seconds:
                break
        except (ConnectionError, TimeoutError):
            if ready_at is not None:
                raise
        finally:
            connection.close()
        time.sleep(0.25)
    if ready_at is None or not any("LISTENING" in row for row in rows):
        raise RuntimeError("No backend listener was observed; this is not a valid capture.")
    remote = [row for row in rows if row.split()[3] != "LISTENING"
              and not ipaddress.ip_address(row.split()[2].rsplit(":", 1)[0].strip("[]")).is_loopback]
    return {"samples": len(statuses), "seconds_after_ready": time.monotonic() - ready_at,
            "tcp_rows": sorted(rows), "remote_tcp": remote}


def observe(task_dir: Path, setting: str, netstat: str, seconds: float) -> dict[str, object]:
    from hermes_cli.config import atomic_config_write

    task_home = task_dir / setting
    task_home.mkdir()
    if setting == "false":
        atomic_config_write(task_home / "config.yaml", {"updates": {"check": False}})
    token = secrets.token_urlsafe(32)
    env = {key: value for key, value in os.environ.items()
           if key.upper() in {"PATH", "PATHEXT", "SYSTEMROOT", "COMSPEC", "TEMP", "TMP"}}
    env.update(HERMES_HOME=str(task_home), HERMES_RUNTIME_DIR=str(task_dir / "runtime"),
               USERPROFILE=str(task_dir), HOME=str(task_dir), HERMES_DASHBOARD_SESSION_TOKEN=token,
               LOCALAPPDATA=str(task_dir / "AppData" / "Local"), APPDATA=str(task_dir / "AppData" / "Roaming"),
               PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    command = [sys.executable, str(Path(__file__).resolve()), "--home", str(task_home)]
    probe_audit = task_dir / f"{setting}-probe.jsonl"
    probe = subprocess.run([*command, "--child", "probe", "--audit", str(probe_audit)], env=env,
                           cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=True, timeout=30)
    probe_status = json.loads(probe.stdout)
    if probe_status.get("reason") != "disabled":
        raise RuntimeError("The desktop source probe was not disabled.")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    server_audit = task_dir / f"{setting}-server.jsonl"
    with (task_dir / f"{setting}-server.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen([*command, "--child", "server", "--audit", str(server_audit),
                                    "--port", str(port)], env=env, cwd=ROOT, stdout=log, stderr=log)
        try:
            report = poll_backend(process, netstat, port, token, seconds)
        finally:
            import psutil

            try:
                descendants = psutil.Process(process.pid).children(recursive=True)
            except psutil.NoSuchProcess:
                descendants = []
            if process.poll() is None:
                process.terminate()
            for child in descendants:
                try:
                    child.terminate()
                except psutil.NoSuchProcess:
                    continue
            process.wait(timeout=15)
            _, alive = psutil.wait_procs(descendants, timeout=15)
            for child in alive:
                child.kill()
            _, alive = psutil.wait_procs(alive, timeout=15)
            if alive:
                raise RuntimeError("Probe backend processes did not stop.")
    attempts = [json.loads(line) for path in (probe_audit, server_audit)
                for line in path.read_text(encoding="utf-8-sig").splitlines()]
    remote_attempts = [item for item in attempts if not ipaddress.ip_address(item["address"][0]).is_loopback]
    return {"setting": setting, "probe_reason": probe_status["reason"], **report,
            "python_connects": attempts, "remote_python_connects": remote_attempts}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child", choices=("probe", "server"))
    parser.add_argument("--home", type=Path)
    parser.add_argument("--audit", type=Path)
    parser.add_argument("--port", type=int)
    parser.add_argument("--seconds", type=float, default=10)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.child:
        run_child(args)
        return
    if not math.isfinite(args.seconds) or args.seconds <= 0:
        parser.error("--seconds must be a finite positive number.")
    from hermes_platform.resolver import locate_command

    netstat = next(candidate.value for candidate in locate_command("netstat").candidates if candidate.present)
    with tempfile.TemporaryDirectory(prefix="hermes-t11-") as scratch:
        reports = [observe(Path(scratch), setting, netstat, args.seconds) for setting in ("missing", "false")]
    output = json.dumps({"observed_at": datetime.now(UTC).isoformat(),
                         "scope": "Python passive updates and standalone serve TCP only", "reports": reports},
                        ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(output + "\n", encoding="utf-8")
    print(output)
    if any(report["remote_tcp"] or report["remote_python_connects"] for report in reports):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
