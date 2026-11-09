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
    assert discovery.SERVICE_NAME == "KouriChat Next"


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
