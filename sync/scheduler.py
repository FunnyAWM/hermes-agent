"""Periodic role-aware sync loop for KouriChat Link.

Android keeps this shape too: no resident background service yet (同 12 placeholder) — the
scheduler runs wherever a long-lived process (``hermes sync watch``, the desktop serve
backend) opts in, syncing once per configured interval. The master additionally serves
TLS while the scheduler runs; a failed cycle records the error and keeps looping.
"""

from __future__ import annotations

import contextvars
import threading
from typing import Callable

from sync.errors import SyncError
from sync.manager import ROLE_MASTER, SyncManager


class SyncScheduler:
    def __init__(
        self,
        manager: SyncManager,
        interval_minutes: int,
        *,
        ensure_server: bool = True,
        on_error: Callable[[SyncError], None] | None = None,
    ) -> None:
        if interval_minutes < 1:
            raise ValueError("interval must be at least 1 minute (0 = manual only)")
        self._manager = manager
        self._interval_s = interval_minutes * 60
        self._ensure_server = ensure_server
        self._on_error = on_error
        self._stop = threading.Event()

    def run_forever(self) -> None:
        """Foreground loop: sync immediately, then once per interval until stopped."""
        serving = self._maybe_start_server()
        try:
            while not self._sync_once():
                self._stop.wait(self._interval_s)
        finally:
            if serving:
                self._manager.stop_server()

    def start(self) -> None:
        """Background loop for long-lived hosts; :meth:`stop` ends it. Runs under the
        spawner's context snapshot: the manager's stores resolve config/.env per current
        scope, so a multiplexing host's loop must keep the owning profile's."""
        if self._stop.is_set():
            self._stop.clear()
        scope = contextvars.copy_context()
        threading.Thread(target=scope.run, args=(self.run_forever,), daemon=True, name="kourichat-link-scheduler").start()

    def stop(self) -> None:
        self._stop.set()

    def _sync_once(self) -> bool:
        """One cycle; returns True when stopped (so the loop exits without waiting)."""
        if self._stop.is_set():
            return True
        try:
            self._manager.sync_now()
        except SyncError as error:
            if self._on_error is not None:
                self._on_error(error)
        except OSError as error:  # transient local I/O: keep the loop alive
            if self._on_error is not None:
                self._on_error(SyncError.from_exception(error))
        return self._stop.is_set()

    def _maybe_start_server(self) -> bool:
        if not self._ensure_server or self._manager.status().role != ROLE_MASTER:
            return False
        try:
            self._manager.start_server()
            return True
        except SyncError as error:
            if self._on_error is not None:
                self._on_error(error)
            return False
