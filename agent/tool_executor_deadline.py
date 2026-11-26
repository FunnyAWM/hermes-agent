"""Per-call deadline floors: a tool that declares how long one call may block
(``ToolEntry.deadline_floor``) has the executor's generic deadline raised to clear it."""

# A tool that declares a per-call budget (ToolEntry.deadline_floor) gets the generic deadline
# raised to it plus this grace, so the tool's OWN timeout is the one that fires: it returns the
# partial output and exit code it collected, where the generic guard discards both and reports
# only ``tool_timeout``.
_DECLARED_DEADLINE_GRACE_S = 5.0


def declared_deadline_floor(name: str, args: dict) -> float | None:
    """Lower bound this call's deadline must clear, from the tool's own declaration."""
    from tools.registry import registry

    floor = registry.get_deadline_floor(name, args)
    return None if floor is None else floor + _DECLARED_DEADLINE_GRACE_S


def deadline_with_floor(generic: float | None, floor: float | None) -> float | None:
    """Raise *generic* to *floor*. A disabled generic deadline (``None``) stays unbounded —
    a floor only ever lengthens a deadline, it never introduces one."""
    if generic is None or floor is None:
        return generic
    return max(generic, floor)


def batch_deadline(generic: float | None, parsed_calls) -> float | None:
    """One deadline covers a whole concurrent batch, so it has to clear the longest budget any
    member declared — otherwise one slow-but-valid call takes every sibling down with it."""
    for call in parsed_calls:
        if call.parse_error is None:
            generic = deadline_with_floor(generic, declared_deadline_floor(call.name, call.args))
    return generic


# Fallback only; the effective bound derives from approvals.timeout (authorization_gate_lock_timeout).
_AUTHORIZATION_GATE_LOCK_TIMEOUT_S = 360.0


def authorization_gate_lock_timeout() -> float:
    """Authorization-lock bound = ``tools.approval_human_wait.human_wait_ceiling`` (approval timeout +
    margin, capped so it can't overflow Lock.acquire): never break serialization while a
    prompt is answerable, never let a wedged holder park workers forever. Deliberately NOT
    min()'d with the fallback so the gate never gives up early.

    Delegates to ``tools.approval_human_wait.human_wait_ceiling`` — the same bound that clamps a human-wait window's
    deadline contribution — so the two can't drift. Long enough that serialization is never broken while a
    legitimate approval prompt is still answerable; short enough that a wedged holder (hanging
    ``pre_tool_call`` plugin, dead approval client) cannot park other workers forever (#79719). Resolved
    once per gate (per batch), so a mid-process ``approvals.timeout`` change applies from the next batch.
    """
    try:
        from tools.approval_human_wait import human_wait_ceiling

        # human_wait_ceiling is platform-safety-capped (agent/deadline.py MAX_SAFE_TIMEOUT_S): a huge
        # approvals.timeout can no longer overflow Lock.acquire's time_t on macOS (#83220). Deliberately NOT
        # min()'d with _AUTHORIZATION_GATE_LOCK_TIMEOUT_S — the gate must never give up while a legitimate
        # approval prompt is still answerable (#79719), so a configured approvals.timeout above 360s must
        # extend the gate.
        return human_wait_ceiling()
    except Exception:
        return _AUTHORIZATION_GATE_LOCK_TIMEOUT_S
