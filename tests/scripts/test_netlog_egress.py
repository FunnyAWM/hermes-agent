"""T-11: NetLog / audit host extraction and default-egress classification."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "capture_desktop_network.py"


def _load():
    spec = importlib.util.spec_from_file_location("capture_network_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


capture = _load()


def _netlog(*events) -> dict:
    return {"constants": {}, "events": list(events)}


def test_hosts_from_dns_url_quic_and_socket_events():
    payload = _netlog(
        {"type": "HOST_RESOLVER_MANAGER_REQUEST", "params": {"host": "api.example.test"}},
        {"type": "URL_REQUEST_START_JOB", "params": {"url": "https://models.example.test/v1/chat"}},
        {"type": "QUIC_SESSION", "params": {"host": "quic.example.test", "port": 443}},
        {"type": "SOCKET_CONNECT", "params": {"address": "203.0.113.9:443"}},
        {"type": "SOCKET_CONNECT", "params": {"address": "[::1]:9119"}},
    )
    hosts = capture.hosts_from_netlog(payload)
    assert {"api.example.test", "models.example.test", "quic.example.test", "203.0.113.9", "::1"} <= hosts


def test_non_network_events_are_ignored():
    payload = _netlog(
        {"type": "LOG", "params": {"message": "https://github.com/should/not/count"}},
        {"type": "URL_REQUEST_START_JOB", "params": {"url": "not a url"}},
    )
    assert capture.hosts_from_netlog(payload) == set()


def test_malformed_netlog_is_empty_not_fatal():
    assert capture.hosts_from_netlog({}) == set()
    assert capture.hosts_from_netlog({"events": None}) == set()


def test_loopback_is_exempt():
    result = capture.classify({"127.0.0.1", "::1", "localhost"})
    assert result["violations"] == []
    assert set(result["loopback"]) == {"127.0.0.1", "::1", "localhost"}


def test_allowed_model_endpoint_passes():
    result = capture.classify({"models.example.test"}, {"models.example.test"})
    assert result["allowed"] == ["models.example.test"]
    assert result["violations"] == []


def test_passive_update_domain_is_always_a_violation():
    result = capture.classify({"api.github.com", "raw.githubusercontent.com"})
    reasons = {item["host"]: item["reason"] for item in result["violations"]}
    assert reasons["api.github.com"] == "passive-update domain"
    assert reasons["raw.githubusercontent.com"] == "passive-update domain"


def test_unexpected_host_is_a_violation():
    result = capture.classify({"telemetry.example.test"}, {"models.example.test"})
    assert result["violations"] == [
        {"host": "telemetry.example.test", "reason": "unexpected non-loopback target"}]


def test_audit_lines_merge_and_skip_garbage():
    lines = [
        json.dumps({"event": "socket.connect", "host": "127.0.0.1", "port": 9119}),
        json.dumps({"event": "socket.getaddrinfo", "host": "models.example.test"}),
        "{not json",
    ]
    assert capture.hosts_from_audit(lines) == {"127.0.0.1", "models.example.test"}
