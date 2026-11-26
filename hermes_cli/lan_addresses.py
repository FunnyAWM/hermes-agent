"""LAN URLs for a non-loopback dashboard bind (T-09b / PRD §6.6 远 01).

The dashboard stays loopback-only unless the operator binds it to a LAN address; when they do, the
startup banner should say which addresses a phone can actually reach instead of printing
``0.0.0.0``. Enumeration is IPv4-only: a LAN IPv6 address is rarely stable (privacy extensions,
temporary addresses) and would mislead more often than help.
"""

from __future__ import annotations

import ipaddress
import socket

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
WILDCARD_HOSTS = frozenset({"0.0.0.0", "::", "[::]", ""})


def _bare(host: str) -> str:
    host = (host or "").strip().lower()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    return host


def is_loopback_host(host: str) -> bool:
    bare = _bare(host)
    if bare in LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(bare).is_loopback
    except ValueError:
        return False


def _usable(address: str) -> bool:
    try:
        parsed = ipaddress.IPv4Address(address)
    except ValueError:
        return False
    return not (parsed.is_loopback or parsed.is_link_local or parsed.is_unspecified or parsed.is_multicast)


def _interface_addresses() -> list[str]:
    try:
        import psutil
    except ImportError:  # pragma: no cover - psutil ships with the PC runtime
        return []
    addresses: list[str] = []
    for entries in psutil.net_if_addrs().values():
        for entry in entries:
            if entry.family == socket.AF_INET and _usable(entry.address):
                addresses.append(entry.address)
    return addresses


def enumerate_lan_urls(port: int, *, scheme: str = "http") -> list[str]:
    """Deduped ``scheme://<lan-ipv4>:<port>`` URLs, sorted for a stable banner."""
    return [f"{scheme}://{address}:{port}" for address in sorted(set(_interface_addresses()))]


def lan_banner_lines(host: str, port: int, *, scheme: str = "http") -> list[str]:
    """Extra startup lines for a non-loopback bind; empty for a loopback bind."""
    bare = _bare(host)
    if is_loopback_host(bare):
        return []
    if bare in WILDCARD_HOSTS:
        urls = enumerate_lan_urls(port, scheme=scheme)
    elif _usable(bare):
        urls = [f"{scheme}://{bare}:{port}"]
    else:
        return []
    if not urls:
        return ["  No LAN IPv4 address found; phones cannot reach this bind yet."]
    lines = [f"  Kouri Harness Web UI (LAN) → {url}" for url in urls]
    lines.append("  Pair a phone with: hermes device-pair generate")
    return lines
