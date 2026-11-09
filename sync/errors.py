"""Sync error taxonomy, mirroring the Android error codes (VALIDATION §1, HM-6001–6006).

The CLI and desktop surface these to the user verbatim; the recoverable/fatal split
follows the Android client so retry guidance stays consistent across the ecosystem.
"""

from __future__ import annotations

from enum import Enum


class SyncErrorCode(Enum):
    #: Master unreachable / network failure; retry with backoff.
    UNREACHABLE = "HM-6001"
    #: Not paired (no mnemonic / no chain membership); pairing is required.
    NOT_PAIRED = "HM-6002"
    #: Verification failure: AAD, digest, TOFU pin or protocol shape; retry may help.
    VERIFY = "HM-6003"
    #: On-device sync state is corrupt; fatal, restore from pairing.
    STATE_CORRUPT = "HM-6004"
    #: Challenge-response failed (wrong mnemonic); fatal for the session.
    AUTH = "HM-6005"
    #: The sync server failed to start (port in use, TLS identity problems); retryable.
    SERVER_START = "HM-6006"

    @property
    def wire_name(self) -> str:
        """The Android ``AppError`` enum name sent as a wire ``error.reason``."""
        return f"SYNC_{self.name}"


class SyncError(Exception):
    def __init__(self, code: SyncErrorCode, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code.value}: {detail}" if detail else code.value)

    @classmethod
    def from_exception(cls, error: Exception) -> SyncError:
        """Map a foreign exception onto the nearest recoverable code (bookkeeping paths)."""
        return cls(SyncErrorCode.UNREACHABLE, str(error))
