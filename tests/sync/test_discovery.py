"""Discovery records must be legal DNS-SD.

``zeroconf`` rejects a service type that does not end in ``.local.``. That rejection used
to be swallowed by the "discovery is optional" except blocks, so both registration and
browsing became silent no-ops and address-free pairing found nothing while everything
looked wired up — hence the constants are pinned here.
"""

from __future__ import annotations

import socket

import pytest

from sync import discovery


def test_service_type_is_spelled_for_zeroconf():
    # Android passes the bare `_kourichat-sync._tcp` to NSD, which expands it to exactly
    # this on the wire; both ends must therefore agree on the `.local.` suffix.
    assert discovery.SERVICE_TYPE == "_kourichat-sync._tcp.local."
    assert discovery.SERVICE_NAME == "Kouri Harness"


def test_service_info_accepts_the_constants():
    zeroconf = pytest.importorskip("zeroconf", reason="optional LAN discovery dependency")
    info = zeroconf.ServiceInfo(
        discovery.SERVICE_TYPE,
        f"{discovery.SERVICE_NAME}.{discovery.SERVICE_TYPE}",
        port=27890,
        properties={},
        server="probe.local.",
        addresses=[socket.inet_aton("127.0.0.1")],
    )
    assert info.port == 27890
    assert info.type == discovery.SERVICE_TYPE


def test_zeroconf_available_follows_the_import(monkeypatch):
    monkeypatch.setattr(discovery, "_zeroconf", lambda: object())
    assert discovery.zeroconf_available() is True
    monkeypatch.setattr(discovery, "_zeroconf", lambda: None)
    assert discovery.zeroconf_available() is False


def test_lan_addresses_drop_loopback(monkeypatch):
    class _Probe:
        def __init__(self, *args, **kwargs):
            pass

        def connect(self, _target):
            pass

        def getsockname(self):
            return ("192.168.1.20", 51234)

        def close(self):
            pass

    monkeypatch.setattr(discovery.socket, "socket", lambda *a, **k: _Probe())
    monkeypatch.setattr(discovery.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("192.168.1.20", 0)), (2, 1, 6, "", ("127.0.0.1", 0))])
    assert discovery.lan_ipv4_addresses() == ["192.168.1.20"]


def test_probe_subnet_tries_the_whole_24_and_keeps_open_hosts(monkeypatch):
    monkeypatch.setattr(discovery, "lan_ipv4_addresses", lambda: ["192.168.1.20"])
    tried: list[tuple[str, int]] = []

    class _Open:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def connect(target, timeout=None):
        tried.append(target)
        if target[0] == "192.168.1.77":
            return _Open()
        raise OSError("refused")

    monkeypatch.setattr(discovery.socket, "create_connection", connect)
    assert discovery.probe_subnet(27890, 3000) == [("192.168.1.77", 27890)]
    # 254 neighbours minus this host itself.
    assert len(tried) == 253
    assert ("192.168.1.20", 27890) not in tried


def test_discover_falls_back_to_the_subnet_probe(monkeypatch):
    """mDNS is filtered on most Windows LANs: browsing empty must not mean "not found"."""
    instance = discovery.LinkDiscovery()
    monkeypatch.setattr(instance, "_browse", lambda timeout_ms: [])
    monkeypatch.setattr(discovery, "probe_subnet", lambda port, timeout_ms: [("10.0.0.9", port)])

    assert instance.discover(1_000, port=27890) == [("10.0.0.9", 27890)]


def test_discover_keeps_mdns_answers_first(monkeypatch):
    instance = discovery.LinkDiscovery()
    monkeypatch.setattr(instance, "_browse", lambda timeout_ms: [("10.0.0.5", 27890)])
    monkeypatch.setattr(discovery, "probe_subnet", lambda port, timeout_ms: [("10.0.0.9", port), ("10.0.0.5", port)])

    assert instance.discover(1_000, port=27890) == [("10.0.0.5", 27890), ("10.0.0.9", 27890)]
