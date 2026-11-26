"""T-09b: LAN banner enumeration for a non-loopback dashboard bind."""

from __future__ import annotations

import socket
from types import SimpleNamespace

import psutil
import pytest

from hermes_cli import lan_addresses


def _entry(address: str, family: int = socket.AF_INET):
    return SimpleNamespace(family=family, address=address)


@pytest.fixture
def interfaces(monkeypatch):
    def install(mapping):
        monkeypatch.setattr(psutil, "net_if_addrs", lambda: mapping)
    return install


@pytest.fixture
def addresses(monkeypatch):
    def install(values):
        monkeypatch.setattr(lan_addresses, "_interface_addresses", lambda: list(values))
    return install


def test_loopback_hosts_never_get_a_lan_banner(addresses):
    addresses(["192.168.1.5"])
    for host in ("127.0.0.1", "localhost", "::1", "[::1]", "127.0.0.2"):
        assert lan_addresses.is_loopback_host(host), host
        assert lan_addresses.lan_banner_lines(host, 9119) == [], host
    assert not lan_addresses.is_loopback_host("0.0.0.0")
    assert not lan_addresses.is_loopback_host("192.168.1.5")


def test_multi_nic_addresses_are_deduped_and_ipv6_filtered(interfaces):
    interfaces({
        "Ethernet": [_entry("192.168.1.5"), _entry("fe80::1234", socket.AF_INET6),
                     _entry("169.254.10.1")],
        "Wi-Fi": [_entry("10.0.0.7"), _entry("192.168.1.5")],
        "Loopback": [_entry("127.0.0.1")],
    })
    assert lan_addresses.enumerate_lan_urls(9119) == [
        "http://10.0.0.7:9119", "http://192.168.1.5:9119"]


def test_loopback_only_host_yields_no_urls(interfaces):
    interfaces({"Loopback": [_entry("127.0.0.1")], "vEthernet": [_entry("169.254.3.3")]})
    assert lan_addresses.enumerate_lan_urls(9119) == []


def test_wildcard_bind_lists_urls_and_pairing_hint(addresses):
    addresses(["192.168.1.5"])
    lines = lan_addresses.lan_banner_lines("0.0.0.0", 9119)
    assert lines[0] == "  Kouri Harness Web UI (LAN) → http://192.168.1.5:9119"
    assert any("hermes device-pair generate" in line for line in lines)


def test_explicit_lan_bind_uses_that_address(addresses):
    addresses(["10.0.0.7"])
    lines = lan_addresses.lan_banner_lines("192.168.1.5", 9119)
    assert lines[0].endswith("http://192.168.1.5:9119")


def test_no_lan_address_is_stated_not_silently_empty(addresses):
    addresses([])
    lines = lan_addresses.lan_banner_lines("0.0.0.0", 9119)
    assert len(lines) == 1
    assert "No LAN IPv4 address found" in lines[0]
