"""KouriChat Link device-chain sync (``methods_sync.py``): pairing and sync state.

Every method takes the optional profile param: sync state lives in that profile's
HERMES_HOME (``.meta/state.json``, the ``.env`` mnemonic, the config.yaml role), so a
multiplexing backend answers per profile and never quietly for the launch profile.
Operations map onto :mod:`hermes_cli.sync_cmd`'s stores and :mod:`sync.manager`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .base import Result
from .common import ProfileParams
from .registry import method

SyncRole = Literal["disabled", "master", "secondary"]


class SyncStatusParams(ProfileParams):
    pass


class SyncStatusResult(Result):
    role: SyncRole
    paired: bool
    device_id: str | None = None
    master_endpoint: str = ""
    listen_port: int = 27890
    auto_sync_minutes: int = 0
    pinned_fingerprint: str | None = None
    last_sync_at_ms: int | None = None
    last_sync_error: str | None = None
    applied_count: int = 0
    archived_count: int = 0
    tracked_files: int = 0
    syncable_files: int = 0
    serving: bool = False


class SyncCreateChainParams(ProfileParams):
    pass


class SyncCreateChainResult(Result):
    """The 24 words are shown exactly once (paper backup) and stored only in .env."""

    words: list[str] = Field(min_length=24, max_length=24)


class SyncPairParams(ProfileParams):
    endpoint: str | None = None  # empty/absent: auto-discover the master on this LAN
    words: list[str] = Field(min_length=24, max_length=24)


class SyncPairResult(Result):
    device_id: str
    pinned_fingerprint: str
    #: The master actually adopted — with an absent endpoint this is the discovered one.
    master_endpoint: str = ""


class SyncNowParams(ProfileParams):
    pass


class SyncNowResult(Result):
    role: SyncRole
    records: int
    applied: int


class SyncServeParams(ProfileParams):
    start: bool


class SyncServeResult(Result):
    serving: bool
    port: int | None = None


class SyncAckResult(Result):
    ok: bool = True


method(
    "sync.status",
    params=SyncStatusParams,
    result=SyncStatusResult,
    doc="Pairing and sync state for one profile's KouriChat Link membership.",
)
method(
    "sync.create_chain",
    params=SyncCreateChainParams,
    result=SyncCreateChainResult,
    doc="Become the chain master: generate the 24-word mnemonic (shown once).",
)
method(
    "sync.pair",
    params=SyncPairParams,
    result=SyncPairResult,
    doc="Join a chain as secondary: full pull + apply, then persist the pairing (all-or-nothing).",
)
method(
    "sync.now",
    params=SyncNowParams,
    result=SyncNowResult,
    doc="Sync once: refresh the record log as master, pull+apply as secondary.",
)
method(
    "sync.serve",
    params=SyncServeParams,
    result=SyncServeResult,
    doc="Start or stop the master-role TLS sync server inside this backend process.",
)
method(
    "sync.disconnect",
    params=SyncCreateChainParams,
    result=SyncAckResult,
    doc="Leave the chain (secondary): pairing is discarded, synced files stay.",
)
method(
    "sync.reset_chain",
    params=SyncCreateChainParams,
    result=SyncAckResult,
    doc="Kill the chain (master): stop serving and discard this device's pairing.",
)
