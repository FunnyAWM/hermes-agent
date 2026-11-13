"""KouriChat Link device-chain sync JSON-RPC handlers (``contracts/sync.py``).

Bodies are rebound onto server.py's globals (method_ctx.bind_module) and reference them
bare (``_ok``, ``_profile_home``, ``_session_profile_runtime_scope``). Every handler
resolves the optional ``profile`` param first: sync state is per-profile, and a
multiplexing backend must never answer for the launch profile by default (HX002/HX004).
"""

from __future__ import annotations

from sync.errors import SyncError

from .method_ctx import HandlerRegistry, bind_module

_registry = HandlerRegistry()
method = _registry.method


def _sync_manager():
    """The profile-scoped manager (the runtime scope's home resolves it)."""
    from hermes_cli.sync_cmd import shared_manager

    return shared_manager()


def _scoped(handler):
    """Run ``handler(rid, params)`` under the optional ``profile`` param's home scope.

    An unknown profile answers the ``PROFILE_UNKNOWN`` envelope — never a JSON-RPC error,
    never a quiet answer for the launch profile instead.
    """

    def wrapped(rid, params: dict) -> dict:
        profile = str(params.get("profile") or "").strip() if isinstance(params, dict) else ""
        home = None
        if profile:
            from hermes_cli import profiles as profiles_mod

            if not profiles_mod.profile_exists(profile):
                return _ok(rid, {"ok": False, "code": "PROFILE_UNKNOWN",
                                 "message": f"Profile '{profile}' does not exist on this backend."})
            home = _profile_home(profile)
        # None = the launch profile: bind its own frozen secret scope like every probe does.
        with _session_profile_runtime_scope({"profile_home": str(home) if home is not None else None}):
            return handler(rid, params)

    return wrapped


def _sync_error(exc: SyncError) -> dict:
    """HM-600x envelope the client branches on (STRUCTURED, not a JSON-RPC error)."""
    return {"ok": False, "code": exc.code.value, "message": exc.detail or exc.code.name.lower()}


def _paired_error(error: ValueError) -> dict:
    return {"ok": False, "code": "ALREADY_PAIRED", "message": str(error)}


@method("sync.status")
@_scoped
def _(rid, params: dict) -> dict:
    manager = _sync_manager()
    status = manager.status()
    return _ok(rid, {
        "ok": True,
        "role": status.role,
        "paired": status.paired,
        "device_id": status.device_id,
        "master_endpoint": status.master_endpoint,
        "listen_port": status.listen_port,
        "auto_sync_minutes": status.auto_sync_minutes,
        "pinned_fingerprint": status.pinned_fingerprint,
        "last_sync_at_ms": status.last_sync_at_ms,
        "last_sync_error": status.last_sync_error,
        "applied_count": status.applied_count,
        "archived_count": status.archived_count,
        "tracked_files": status.tracked_files,
        "syncable_files": status.syncable_files,
        "serving": manager.serving,
    })


@method("sync.create_chain")
@_scoped
def _(rid, params: dict) -> dict:
    try:
        words = _sync_manager().create_chain()
    except SyncError as exc:
        return _ok(rid, _sync_error(exc))
    except ValueError as error:
        return _ok(rid, _paired_error(error))
    # The words are shown exactly once; the desktop page tells the user to write them down.
    return _ok(rid, {"ok": True, "words": words})


@method("sync.pair")
@_scoped
def _(rid, params: dict) -> dict:
    manager = _sync_manager()
    try:
        state = manager.join_chain(list(params["words"]), (str(params.get("endpoint") or "").strip() or None))
    except SyncError as exc:
        return _ok(rid, _sync_error(exc))
    except ValueError as error:
        return _ok(rid, _paired_error(error))
    return _ok(
        rid,
        {
            "ok": True,
            "device_id": state.device_id,
            "pinned_fingerprint": state.pinned_fingerprint,
            # A discovered endpoint lives in settings, never in the state file.
            "master_endpoint": manager.status().master_endpoint,
        },
    )


@method("sync.now")
@_scoped
def _(rid, params: dict) -> dict:
    try:
        outcome = _sync_manager().sync_now()
    except SyncError as exc:
        return _ok(rid, _sync_error(exc))
    return _ok(rid, {"ok": True, "role": outcome.role, "records": outcome.records, "applied": outcome.applied})


@method("sync.serve")
@_scoped
def _(rid, params: dict) -> dict:
    manager = _sync_manager()
    try:
        if params.get("start"):
            server = manager.start_server()
            return _ok(rid, {"ok": True, "serving": True, "port": server.bound_port})
    except SyncError as exc:
        return _ok(rid, _sync_error(exc))
    manager.stop_server()
    return _ok(rid, {"ok": True, "serving": False, "port": None})


@method("sync.disconnect")
@_scoped
def _(rid, params: dict) -> dict:
    _sync_manager().disconnect_and_clear()
    return _ok(rid, {"ok": True})


@method("sync.reset_chain")
@_scoped
def _(rid, params: dict) -> dict:
    _sync_manager().reset_chain()
    return _ok(rid, {"ok": True})


def register(server) -> None:
    # Pair / sync_now do LAN TLS round-trips: every sync method runs on the long pool.
    server._LONG_HANDLERS = server._LONG_HANDLERS | _registry.names()
    bind_module(globals(), server, skip=("_",))
