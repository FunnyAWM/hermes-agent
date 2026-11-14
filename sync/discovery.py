"""LAN master-device discovery (Android 同 01, user request: no manual address entry).

Mirrors the Android ``NsdSyncDiscovery``: the master registers an mDNS service, secondaries
browse for candidates and try each — whether a candidate is on the same chain is decided by
the existing TLS + challenge handshake, never by anything the discovery record carries.
The service type and name match the Android client byte-for-byte so the two ends find
each other on one LAN.

mDNS alone is not enough on a PC: Windows blocks multicast until the firewall rule exists,
many routers drop 5353 between wireless clients, and a VPN adapter can swallow the query.
So browsing is followed by a **direct TCP probe of the local /24** over the sync port —
the same handshake filters whatever answers, so a false positive costs one failed connect,
not a wrong pair.

``zeroconf`` is an OPTIONAL dependency: without it registration is a no-op, browsing
returns nothing, and the subnet probe still finds a master on the same subnet.
"""

from __future__ import annotations

import socket
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

#: Android ``NsdSyncDiscovery.SERVICE_NAME`` / ``SERVICE_TYPE`` — the PC master must
#: advertise the exact same records for a phone to find it (and vice versa). Android
#: passes the bare ``_kourichat-sync._tcp`` to NSD, which expands it to the DNS-SD type
#: below on the wire; ``zeroconf`` wants it spelled out (it rejects a type that does not
#: end in ``.local.``, which would silently disable both register and browse).
SERVICE_NAME = "Kouri Harness"
SERVICE_TYPE = "_kourichat-sync._tcp.local."

#: How long a browse may take before the subnet probe takes over the remaining budget.
_BROWSE_BUDGET_MS = 2_500
#: One TCP connect to a /24 neighbour; short — this only distinguishes open from silent.
_PROBE_TIMEOUT_S = 0.25
#: Probe fan-out. 254 sockets at a time is what a home router comfortably accepts.
_PROBE_WORKERS = 64


def _zeroconf():
    try:
        import zeroconf
    except ImportError:
        return None
    return zeroconf


def zeroconf_available() -> bool:
    """Whether mDNS discovery can work here at all — the caller prints the install hint
    when an address-free pairing found nothing *because* the optional extra is missing."""
    return _zeroconf() is not None


def lan_ipv4_addresses() -> list[str]:
    """Every non-loopback IPv4 this host answers on, primary interface first.

    ``getaddrinfo(gethostname())`` alone is not reliable on Windows (a machine whose
    hostname is not in DNS/hosts resolves to nothing, so a master would advertise an
    empty address list and be invisible). The UDP-connect trick asks the routing table
    which source address would be used for the outside world — that is the LAN address a
    peer can actually reach — and the resolver list is merged in for multi-homed hosts.
    """
    addresses: list[str] = []

    def add(address: str) -> None:
        if address and address not in addresses and address != "0.0.0.0":
            addresses.append(address)

    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # No packet is sent; connect() only selects a route and binds a source address.
        probe.connect(("8.8.8.8", 80))
        add(str(probe.getsockname()[0]))
    except OSError:
        pass
    finally:
        probe.close()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            add(str(info[4][0]))
    except OSError:
        pass
    return [address for address in addresses if not address.startswith("127.")]


def probe_subnet(port: int, timeout_ms: int = 3_000) -> list[tuple[str, int]]:
    """TCP-connect every /24 neighbour on ``port``; returns ``(host, port)`` candidates.

    A home LAN is a /24 in practice and the master's port is fixed, so this needs no
    multicast, no DNS and no firewall exception — it is the fallback that makes discovery
    work on networks where mDNS is filtered. Only reachable hosts are returned; whether
    they belong to this chain is still the handshake's call.
    """
    hosts: list[str] = []
    seen: set[str] = set()
    for local in lan_ipv4_addresses():
        prefix = local.rsplit(".", 1)[0]
        if prefix in seen:
            continue
        seen.add(prefix)
        for last in range(1, 255):
            host = f"{prefix}.{last}"
            if host != local:
                hosts.append(host)
    if not hosts:
        return []
    deadline = time.monotonic() + max(0.1, timeout_ms / 1000)
    found: list[tuple[str, int]] = []

    def attempt(host: str) -> None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        try:
            with socket.create_connection((host, port), timeout=min(_PROBE_TIMEOUT_S, remaining)):
                found.append((host, port))
        except OSError:
            pass

    with ThreadPoolExecutor(max_workers=_PROBE_WORKERS) as pool:
        list(pool.map(attempt, hosts))
    return found


class LinkDiscovery:
    """Register (master) and browse (secondary) for KouriChat Link peers on this LAN."""

    def __init__(self) -> None:
        self._zc: Any = None
        self._info: Any = None

    def register(self, port: int) -> None:
        """Advertise this device as a master on ``port``; no-op without zeroconf."""
        zeroconf = _zeroconf()
        if zeroconf is None:
            return
        try:
            self._zc = zeroconf.Zeroconf()
            addresses = [socket.inet_aton(address) for address in lan_ipv4_addresses()]
            self._info = zeroconf.ServiceInfo(
                SERVICE_TYPE,
                f"{SERVICE_NAME}.{SERVICE_TYPE}",
                port=port,
                properties={},
                server=f"{socket.gethostname()}.local.",
                # An explicit empty list makes the record unanswerable; letting zeroconf
                # enumerate its own interfaces is the safer default for odd adapters.
                addresses=addresses or None,
            )
            self._zc.register_service(self._info)
        except Exception:  # health: allow BLE001 -- discovery is a convenience: any mDNS failure must never block serving, and the fallback is the manual endpoint
            # Discovery is a convenience: an mDNS failure never blocks serving.
            self.unregister()

    def unregister(self) -> None:
        zc, info, self._zc, self._info = self._zc, self._info, None, None
        if zc is None or info is None:
            return
        try:
            zc.unregister_service(info)
        except Exception:  # health: allow BLE001,S110 -- teardown of an optional advertiser: a failed unregister must not mask the real shutdown
            pass
        try:
            zc.close()
        except Exception:  # health: allow BLE001,S110 -- same teardown seam: close() failures on the advertiser are irrelevant to the shutdown path
            pass

    def discover(self, timeout_ms: int = 4_000, *, port: int | None = None) -> list[tuple[str, int]]:
        """Candidates as ``(host, port)``: mDNS answers first, then a /24 TCP probe.

        Same-chain-ness is the handshake's job, not the record's. ``port`` is the sync
        port to probe when mDNS yields nothing (defaults to the protocol port).
        """
        if port is None:
            from sync.protocol import DEFAULT_PORT

            port = DEFAULT_PORT
        found = self._browse(min(timeout_ms, _BROWSE_BUDGET_MS))
        # mDNS is best-effort on a PC; the probe covers a filtered or absent multicast.
        remaining = max(500, timeout_ms - _BROWSE_BUDGET_MS)
        for candidate in probe_subnet(port, remaining):
            if candidate not in found:
                found.append(candidate)
        return found

    def _browse(self, timeout_ms: int) -> list[tuple[str, int]]:
        zeroconf = _zeroconf()
        if zeroconf is None:
            return []
        try:
            zc = zeroconf.Zeroconf()
        except Exception:  # health: allow BLE001 -- no advertiser/no browse: discovery is optional and the caller falls back to the manual endpoint
            return []
        found: list[tuple[str, int]] = []

        class _Listener:
            def add_service(self, zc_ref, service_type, name) -> None:
                info = zc_ref.get_service_info(service_type, name, timeout=2_000)
                if info is None:
                    return
                # Every address the record carries is a candidate: a peer that advertises
                # both loopback and LAN (or a stale interface) must stay reachable, so the
                # handshake, not the record order, decides. IPv4 only — the sync client
                # connects over v4 and inet_ntoa cannot read a 16-byte address.
                for raw in info.addresses:
                    if len(raw) != 4:
                        continue
                    candidate = (socket.inet_ntoa(raw), info.port)
                    if candidate not in found:
                        found.append(candidate)

            def update_service(self, zc_ref, service_type, name) -> None:
                self.add_service(zc_ref, service_type, name)

            def remove_service(self, zc_ref, service_type, name) -> None:
                pass

        try:
            zeroconf.ServiceBrowser(zc, SERVICE_TYPE, _Listener())
            deadline = time.monotonic() + max(0.2, timeout_ms / 1000)
            while time.monotonic() < deadline and not found:
                time.sleep(0.1)
            time.sleep(0.2)  # let answers arriving in the same instant land
        except Exception:  # health: allow BLE001,S110 -- a failed browse returns no candidates by contract; the peer pair then just uses the saved endpoint
            pass
        finally:
            try:
                zc.close()
            except Exception:  # health: allow BLE001,S110 -- browsing is optional; a close failure after the timeout must not mask the (possibly empty) result
                pass
        return found
