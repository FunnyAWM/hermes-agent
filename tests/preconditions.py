"""Host capability gates shared by tests that need an external tool or egress.

Skips are deliberate and named: a lane that lacks the capability still
collects the test (so CI reports it), while the local run explains exactly
what is missing instead of failing on an opaque child-process error.
"""

from __future__ import annotations

import shutil
import socket

import pytest


def tool_path(name: str) -> str | None:
    """Absolute path of ``name`` on this host, or None when it is not installed."""
    return shutil.which(name)


def requires_tool(name: str, *, reason: str | None = None):
    """Skip marker for a test that needs an executable this host may not have."""
    return pytest.mark.skipif(
        tool_path(name) is None,
        reason=reason or f"{name} is not installed on this host",
    )


def network_available(
    host: str = "github.com", port: int = 443, timeout: float = 1.5
) -> bool:
    """True when a TCP connection to ``host:port`` can be opened right now."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def requires_network(*, reason: str | None = None):
    """Skip marker for a test that performs a real download."""
    return pytest.mark.skipif(
        not network_available(),
        reason=reason or "this host has no outbound network access",
    )
