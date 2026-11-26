"""Compatibility contracts for read-only managed worker sessions."""


def test_read_only_session_fields_default_false_and_round_trip_true():
    from tui_gateway.contracts.canonical_sessions import CanonicalSessionListRow, CanonicalSnapshotInfo
    from tui_gateway.contracts.common import SessionLiveInfo
    from tui_gateway.contracts.sessions import SessionListRow

    assert SessionLiveInfo.model_validate({}).read_only is False
    assert SessionListRow.model_validate({"id": "legacy"}).read_only is False
    assert CanonicalSessionListRow.model_validate({
        "session_id": "legacy",
        "id": "legacy",
        "title": "",
        "source": "gui",
        "started_at": None,
        "message_count": 0,
        "running": False,
    }).read_only is False

    assert SessionLiveInfo.model_validate({"read_only": True}).read_only is True
    assert SessionListRow.model_validate({
        "id": "worker", "source": "managed_worker", "read_only": True
    }).read_only is True
    assert CanonicalSnapshotInfo.model_validate({
        "desktop_protocol": "hermes-gateway-v1",
        "source": "managed_worker",
        "read_only": True,
    }).read_only is True
