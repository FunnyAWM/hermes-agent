"""Shared in-memory stores for sync tests: the real settings/.env stores are CLI seams
and are faked here so tests never touch config.yaml or ~/.hermes/.env."""

from __future__ import annotations

import copy

from sync.manager import SyncSettings


class MemorySettingsStore:
    def __init__(self, settings: SyncSettings | None = None) -> None:
        self._settings = settings or SyncSettings()

    def load(self) -> SyncSettings:
        return copy.copy(self._settings)

    def update(self, settings: SyncSettings) -> None:
        self._settings = copy.copy(settings)


class MemoryMnemonicStore:
    def __init__(self) -> None:
        self._words: list[str] | None = None

    def load(self) -> list[str] | None:
        return list(self._words) if self._words else None

    def save(self, words: list[str]) -> None:
        self._words = list(words)

    def clear(self) -> None:
        self._words = None
