"""Hybrid logical clock for KouriChat Link record ordering.

Mirrors the Android ``SyncClock.kt``: ``{wallMs, counter, deviceId}`` forms a total order.
Newness is decided entirely by the comparing client — neither the server nor the master
assigns versions or arbitrates content age.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True, order=True)
class SyncClock:
    wall_ms: int
    counter: int
    device_id: str

    def tick(self, now_ms: int) -> SyncClock:
        """Local event: the counter resets when the wall clock advances, else increments
        (concurrent events within the same millisecond)."""
        if now_ms > self.wall_ms:
            return SyncClock(now_ms, 0, self.device_id)
        return SyncClock(self.wall_ms, self.counter + 1, self.device_id)

    def recv(self, other: SyncClock, now_ms: int) -> SyncClock:
        """Advance after a remote event: take the max of both wall clocks and the current
        time; tied wall clocks follow Lamport semantics (larger counter, then increment)."""
        wall = max(self.wall_ms, other.wall_ms, now_ms)
        if wall == now_ms and now_ms > self.wall_ms and now_ms > other.wall_ms:
            counter = 0
        elif wall == self.wall_ms and wall == other.wall_ms:
            counter = max(self.counter, other.counter) + 1
        elif wall == self.wall_ms:
            counter = self.counter + 1
        else:
            counter = other.counter
        return SyncClock(wall, counter, self.device_id)

    def canonical(self) -> str:
        """Stable string used for AAD binding and logs."""
        return f"{self.wall_ms}:{self.counter}:{self.device_id}"
