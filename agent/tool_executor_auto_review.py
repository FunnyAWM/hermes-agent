"""``approvals.mode: auto`` gate for one tool call, split out of the executor facade."""

from __future__ import annotations

from typing import Any


def auto_review_block(ref: Any, *, messages: list | None, authorization_gate: Any):
    """``approvals.mode: auto`` gate for one call; returns ``(block_body, error_type)``."""
    from tools.auto_review import gate_tool_call

    return gate_tool_call(
        ref.name, ref.args, messages=messages,
        serialize=None if authorization_gate is None else authorization_gate.run,
    )