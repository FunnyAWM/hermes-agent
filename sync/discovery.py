"""LAN master-device discovery (Android 同 01, user request: no manual address entry).

Mirrors the Android ``NsdSyncDiscovery``: the master registers an mDNS service, secondaries
browse for candidates and try each — whether a candidate is on the same chain is decided by
the existing TLS + challenge handshake, never by anything the discovery record carries.
The service type and name match the Android client byte-for-byte so the two ends find
each other on one LAN.

``zeroconf`` is an OPTIONAL dependency: when it is not installed, registration is a no-op
and discovery returns no candidates (pairing falls back to the manual host:port entry).
"""

from __future__ import annotations

import socket
import time
from typing import Any

#: Android ``NsdSyncDiscovery.SERVICE_NAME`` / ``SERVICE_TYPE`` — the PC master must
#: advertise the exact same records for a phone to find it (and vice versa). Android
#: passes the bare ``_kourichat-sync._tcp`` to NSD, which expands it to the DNS-SD type
#: below on the wire; ``zeroconf`` wants it spelled out (it rejects a type that does not
#: end in ``.local.``, which would silently disable both register and browse).
SERVICE_NAME = "KouriChat Next"
SERVICE_TYPE = "_kourichat-sync._tcp.local."

_DISCOVERY_TIMEOUT_S = 4.0


def _zeroconf():
    try:
        import zeroconf
    except ImportError:
        return None
    return zeroconf


def zeroconf_available() -> bool:
    """Whether discovery can work here at all — the caller prints the install hint when
    an address-free pairing found nothing *because* the optional extra is missing."""
    return _zeroconf() is not None


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
            self._info = zeroconf.ServiceInfo(
                SERVICE_TYPE,
                f"{SERVICE_NAME}.{SERVICE_TYPE}",
                port=port,
                properties={},
                server=f"{socket.gethostname()}.local.",
                addresses=[socket.inet_aton(address) for address in _lan_addresses()],
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

    def discover(self, timeout_ms: int = 4_000) -> list[tuple[str, int]]:
        """Candidates as ``(host, port)``; empty without zeroconf or before the timeout
        finds any. Same-chain-ness is the handshake's job, not the record's."""
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
            deadline = time.monotonic() + timeout_ms / 1000
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


def _lan_addresses() -> list[str]:
    addresses: list[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            if address not in addresses:
                addresses.append(address)
    except OSError:
        pass
    return addresses
