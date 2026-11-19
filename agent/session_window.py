"""Per-session sliding and frozen conversation windows.

Ported from KouriChat Next (Android) 设 08 so both clients send the same history:

- ``context.session_window_turns`` (X): keep only the latest X user turns in the request.
  ``0``/absent = send the full history.
- ``context.frozen_window_turns`` (Y): once the transcript reaches X turns, freeze the last
  Y turns as the request prefix and append newer turns after it, so the provider's prompt
  cache keeps hitting until the window refills to X and re-freezes.

Frozen re-freeze schedule (stateless, so it survives process restarts the way Android's
in-memory freeze does not): at ``total == X`` keep ``Y``; every later turn adds one until
``total == X + (X - Y)``, where the prefix re-freezes to the latest ``Y``.

The window only shapes the wire copy: persisted transcripts and the UI keep every turn.
"""

from __future__ import annotations

from typing import Any

USER_ROLE = "user"


def _turns(value: Any) -> int:
    """Positive int or 0; a bool, non-number or negative value is off."""
    if isinstance(value, bool):
        return 0
    try:
        turns = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, turns)


def window_settings(config: Any) -> tuple[int, int]:
    """``(window_turns, frozen_window_turns)`` from a config dict.

    Frozen is only effective while ``1 <= Y < X``; any other combination disables it and
    leaves the plain sliding window (the Android save path rejects this with HM-4015).
    """
    section = config.get("context") if isinstance(config, dict) else None
    if not isinstance(section, dict):
        return 0, 0
    window = _turns(section.get("session_window_turns"))
    frozen = _turns(section.get("frozen_window_turns"))
    if window <= 0 or frozen <= 0 or frozen >= window:
        return window, 0
    return window, frozen


def token_budget_settings(config: Any) -> int:
    """``context.window_token_budget`` in tokens; ``0`` (off) for absent/negative/non-numeric.

    Ported from KouriChat Next (Android) ``ChatContextTrimmer.trim``: the request keeps the
    newest rows that fit, the system prompt pays for itself, and the newest row is never cut.
    Off by default — a changing cut point breaks the provider prefix cache.
    """
    section = config.get("context") if isinstance(config, dict) else None
    if not isinstance(section, dict):
        return 0
    return _turns(section.get("window_token_budget"))


def budget_start_index(messages: list[dict[str, Any]], *, budget: int, system_tokens: int,
                       estimate: Any) -> int:
    """First row a token budget keeps; 0 = keep everything.

    Walks newest → oldest accumulating ``estimate(message)`` and stops before the first row
    that would overrun ``budget - system_tokens``. The newest row is kept even when it alone
    overruns (Android ``ChatContextTrimmer.trim`` does the same: never drop the question the
    user just asked).
    """
    if budget <= 0 or not messages:
        return 0
    remaining = budget - max(0, system_tokens)
    used = 0
    start = len(messages)
    for index in range(len(messages) - 1, -1, -1):
        cost = max(0, int(estimate(messages[index]) or 0))
        if start < len(messages) and used + cost > remaining:
            break
        used += cost
        start = index
    return start


def user_turn_count(messages: list[dict[str, Any]]) -> int:
    return sum(1 for message in messages if isinstance(message, dict) and message.get("role") == USER_ROLE)


def kept_turn_count(total: int, window: int, frozen: int) -> int:
    """How many trailing user turns the next request carries."""
    if total < window:
        return total
    if frozen <= 0:
        return window
    cycle = window - frozen
    return frozen + (total - window) % cycle


def request_start_index(
    messages: list[dict[str, Any]], window_turns: int, frozen_window_turns: int
) -> int:
    """Index of the first message the next request keeps; 0 = keep everything.

    The cut always lands on a user row, so no tool result loses its call.
    """
    if window_turns <= 0 or not messages:
        return 0
    total = user_turn_count(messages)
    keep = kept_turn_count(total, window_turns, frozen_window_turns)
    if keep >= total:
        return 0
    skip = total - keep
    for index, message in enumerate(messages):
        if isinstance(message, dict) and message.get("role") == USER_ROLE:
            if skip == 0:
                return index
            skip -= 1
    return 0
