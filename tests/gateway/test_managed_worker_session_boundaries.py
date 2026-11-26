"""Router-owned worker sessions are visible to users but mutable only by their Router."""

import json

import pytest

from tests.gateway.fixtures.storage_handles import close_storage_handles

pytestmark = pytest.mark.usefixtures("authority_cwd_in_tmp")


def _connection(authority, subject, capabilities):
    from gateway.session_controls import AuthorityConnection

    return AuthorityConnection(authority, object(), {
        "user_id": subject,
        "provider": "local",
        "profile_id": authority.profile_id,
        "instance_id": authority.instance_id,
        "capabilities": capabilities,
        "native_bootstrap": True,
    })


async def _call(connection, method, **params):
    return await connection.dispatch({"id": f"{method}:{params.get('request_id', '')}",
                                      "method": method, "params": params})


@pytest.mark.asyncio
async def test_worker_sessions_are_user_read_only_and_router_owned(monkeypatch):
    from gateway import run
    from gateway.session_contract import SessionRef
    from hermes_constants import get_hermes_home
    from tests.gateway.test_session_follow_profile_config import _owner

    home = get_hermes_home()
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(run, "_hermes_home", home)
    (home / "config.yaml").write_text(json.dumps({"model": {"default": "fixture"}}))
    store, _runner, authority, default_owner = _owner(home)
    manager_caps = ["session:create", "session:read", "session:submit", "session:control",
                    "session:manage-worker"]
    user_caps = ["session:create", "session:read", "session:submit", "session:control"]
    manager = _connection(authority, "plugin:super_agent", manager_caps)
    other_manager = _connection(authority, "plugin:other", manager_caps)
    user = _connection(authority, "human", user_caps)
    try:
        created = await _call(manager, "session.create", request_id="worker-1", source="managed_worker",
                              cwd=str(home), toolsets=[])
        assert "result" in created, created
        worker_id = created["result"]["session_id"]
        assert created["result"]["info"]["read_only"] is True

        user_created = await _call(user, "session.create", request_id="user-1", source="gui",
                                   cwd=str(home), toolsets=[])
        assert "result" in user_created, user_created
        user_id = user_created["result"]["session_id"]

        assert (await _call(user, "session.resume", session_id=worker_id))["result"]["info"]["read_only"] is True
        listed = await _call(user, "session.list")
        worker_row = next(row for row in listed["result"]["sessions"] if row["session_id"] == worker_id)
        assert worker_row["read_only"] is True

        manager_rows = (await _call(manager, "session.list"))["result"]["sessions"]
        assert {row["session_id"] for row in manager_rows} == {worker_id, user_id}
        other_rows = (await _call(other_manager, "session.list"))["result"]["sessions"]
        assert {row["session_id"] for row in other_rows} == {user_id}

        worker_ref = SessionRef(authority.profile_id, worker_id)
        authority.authorize(user.actor, worker_ref, "session:read")
        for actor in (user.actor, other_manager.actor):
            for capability in ("session:submit", "session:control"):
                with pytest.raises(Exception) as denied:
                    authority.authorize(actor, worker_ref, capability)
                assert getattr(denied.value, "reason", None) == "permission_denied"
        with pytest.raises(Exception) as denied:
            authority.authorize(other_manager.actor, worker_ref, "session:read")
        assert getattr(denied.value, "reason", None) == "permission_denied"
        authority.authorize(manager.actor, worker_ref, "session:control")

        user_ref = SessionRef(authority.profile_id, user_id)
        authority.authorize(manager.actor, user_ref, "session:read")
        manager_sessions = (await _call(manager, "session.list"))["result"]["sessions"]
        assert any(row["session_id"] == user_id for row in manager_sessions)
        for capability in ("session:submit", "session:control"):
            with pytest.raises(Exception) as denied:
                authority.authorize(manager.actor, user_ref, capability)
            assert getattr(denied.value, "reason", None) == "permission_denied"

        for connection, source in ((manager, "gui"), (user, "managed_worker")):
            refused = await _call(connection, "session.create", request_id=f"forged-{source}", source=source,
                                  cwd=str(home), toolsets=[])
            assert refused["error"]["message"] == "permission_denied"

        authority.persistence.sessions.end(worker_id, "closed")
        authority.sessions.pop(worker_id)
        from gateway.session_listing import _stored_closed
        assert _stored_closed(authority, user.actor, worker_id)
        assert _stored_closed(authority, manager.actor, worker_id)
        assert not _stored_closed(authority, other_manager.actor, worker_id)
    finally:
        await manager.close()
        await other_manager.close()
        await user.close()
        await default_owner.close()
        store.close_all_db_handles()


def test_managed_session_ticket_is_profile_bound_single_use_and_non_operator():
    from gateway.runtime_bootstrap import TicketStore

    store = TicketStore("owner", frozenset({"profile", "other"}))
    ticket = store.mint(profile_id="profile", subject="plugin:super_agent", purpose="managed-session")
    for profile, purpose in (("other", "managed-session"), ("profile", "interactive")):
        with pytest.raises(PermissionError):
            store.redeem(ticket, profile_id=profile, purpose=purpose)
    grant = store.redeem(ticket, profile_id="profile", purpose="managed-session")
    assert "session:manage-worker" in grant["capabilities"]
    assert not ({"session:operator", "session:approve", "session:respond"} & grant["capabilities"])
    with pytest.raises(PermissionError):
        store.redeem(ticket, profile_id="profile", purpose="managed-session")
    with pytest.raises(PermissionError):
        store.mint(
            profile_id="profile", subject="plugin:super_agent", purpose="managed-session", scope="host"
        )
