"""Router-selected sandbox levels are mandatory for Worker terminal calls."""

import pytest

from plugins.super_agent import _sandbox_hook
from plugins.super_agent.store import SuperAgentStore


@pytest.mark.parametrize("level", ["read-only", "workspace-write", "workspace-exec"])
@pytest.mark.parametrize("model_value", [None, "full-access", "unknown"])
def test_terminal_hook_overrides_every_model_sandbox(level, model_value, tmp_path):
    store = SuperAgentStore(tmp_path / "data", "default")
    ticket, _ = store.submit(
        task="task", sandbox=level, workspace_root="workspace",
        source_instance="test", external_event_id=f"{level}:{model_value}", source=None,
    )
    assignment = store.claim_next()
    store.bind_worker_session(assignment["id"], "worker-1")
    args = {"command": "git status"}
    if model_value is not None:
        args["sandbox"] = model_value

    decision = _sandbox_hook(store, "terminal", args, session_id="worker-1")

    assert ticket["sandbox"] == level
    assert decision == {
        "action": "modify",
        "args": {
            "command": "git status",
            "sandbox": level,
            "_host_local": True,
            "_sandbox_required": True,
        },
    }


def test_hook_ignores_other_tools_and_blocks_unknown_or_stale_sessions(tmp_path):
    store = SuperAgentStore(tmp_path / "data", "default")
    ticket, _ = store.submit(
        task="task", sandbox="read-only", workspace_root="workspace",
        source_instance="test", external_event_id="event", source=None,
    )
    assignment = store.claim_next()
    store.bind_worker_session(assignment["id"], "worker-1")

    assert _sandbox_hook(store, "file", {"path": "x"}, session_id="worker-1") is None
    assert _sandbox_hook(store, "terminal", {"command": "pwd"}, session_id="unknown")["action"] == "block"
    store.cancel(ticket["id"])
    assert _sandbox_hook(store, "terminal", {"command": "pwd"}, session_id="worker-1")["action"] == "block"


def test_store_rejects_full_access_before_an_assignment_exists(tmp_path):
    store = SuperAgentStore(tmp_path / "data", "default")
    with pytest.raises(ValueError, match="sandbox"):
        store.submit(
            task="task", sandbox="full-access", workspace_root="workspace",
            source_instance="test", external_event_id="event", source=None,
        )
    assert store.list_tickets() == []
