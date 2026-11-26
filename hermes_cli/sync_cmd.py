"""``hermes sync`` — KouriChat Link device-chain commands.

The hermes seams for :mod:`sync.manager` live here: the role/endpoint/port settings come
from the ``kourichat_link`` section of config.yaml, and the 24-word mnemonic — the sole
root credential — is stored only in ``.env`` (the project's secrets file) under
``KOURICHAT_SYNC_MNEMONIC``.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import replace

from sync.errors import SyncError
from sync.manager import (
    ROLE_DISABLED,
    ROLE_MASTER,
    ROLE_SECONDARY,
    SyncManager,
    SyncSettings,
    parse_endpoint,
)
from sync.scheduler import SyncScheduler

CONFIG_SECTION = "kourichat_link"
MNEMONIC_ENV_KEY = "KOURICHAT_SYNC_MNEMONIC"


def sync_command(args) -> int:
    """Handle ``hermes sync`` subcommands; returns a process exit code."""
    handlers = {
        "pair": lambda: _cmd_pair(args),
        "create-chain": lambda: _cmd_create_chain(),
        "now": lambda: _cmd_now(),
        "status": lambda: _cmd_status(),
        "serve": lambda: _cmd_serve(),
        "watch": lambda: _cmd_watch(),
        "disconnect": lambda: _cmd_disconnect(),
        "reset-chain": lambda: _cmd_reset_chain(args),
    }
    handler = handlers.get(getattr(args, "sync_action", None))
    if handler is None:
        print("Usage: hermes sync {pair|create-chain|now|status|serve|watch|disconnect|reset-chain}")
        print("Run 'hermes sync --help' for details.")
        return 2
    try:
        return handler()
    except SyncError as error:
        print(f"  ✗ {error.code.value}: {error.detail or error.code.name.lower()}")
        return 1


# ── hermes seams ─────────────────────────────────────────────────────────────────


class ConfigSettingsStore:
    """SyncSettings over the ``kourichat_link`` config.yaml section."""

    def load(self) -> SyncSettings:
        from hermes_cli.config import read_raw_config_readonly
        from hermes_cli.config_defaults_kourichat import KOURICHAT_LINK_DEFAULTS

        section = read_raw_config_readonly().get(CONFIG_SECTION) or {}
        return SyncSettings(
            role=section.get("role") or KOURICHAT_LINK_DEFAULTS["role"],
            master_endpoint=section.get("master_endpoint") or "",
            listen_port=_as_int(section.get("listen_port"), KOURICHAT_LINK_DEFAULTS["listen_port"]),
            auto_sync_minutes=_as_int(section.get("auto_sync_minutes"), KOURICHAT_LINK_DEFAULTS["auto_sync_minutes"]),
        )

    def update(self, settings: SyncSettings) -> None:
        from hermes_cli.config import read_raw_config_readonly, save_config

        config = read_raw_config_readonly()
        config[CONFIG_SECTION] = {
            "role": settings.role,
            "master_endpoint": settings.master_endpoint,
            "listen_port": settings.listen_port,
            "auto_sync_minutes": settings.auto_sync_minutes,
        }
        save_config(config, merge_existing=True)


class EnvMnemonicStore:
    """The 24-word mnemonic, stored only in ``.env`` (secrets file)."""

    def load(self) -> list[str] | None:
        from hermes_cli.config_env_routing import read_env_setting

        value = read_env_setting(MNEMONIC_ENV_KEY)
        if not value:
            return None
        return [word for word in value.replace("\n", " ").split(" ") if word]

    def save(self, words: list[str]) -> None:
        from hermes_cli.config_env_routing import save_env_setting

        save_env_setting(MNEMONIC_ENV_KEY, " ".join(words))

    def clear(self) -> None:
        from hermes_cli.config_env_routing import remove_env_setting

        remove_env_setting(MNEMONIC_ENV_KEY)


def section_default(key: str):
    """The shipped default for one kourichat_link key. Held outside DEFAULT_CONFIG:
    ``config_defaults.py`` sits over its line ratchet and may only shrink."""
    from hermes_cli.config_defaults_kourichat import KOURICHAT_LINK_DEFAULTS

    return KOURICHAT_LINK_DEFAULTS[key]


def _as_int(value, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _manager() -> SyncManager:
    from hermes_constants import get_hermes_home

    return SyncManager(
        get_hermes_home(),
        ConfigSettingsStore(),
        EnvMnemonicStore(),
        pre_sync=_export_bot_sessions,
        post_sync=_import_synced_sessions,
    )


def update_settings(*, listen_port=None, auto_sync_minutes=None) -> SyncSettings:
    """Persist user-editable kourichat_link settings; ``None`` leaves a key untouched."""
    store = ConfigSettingsStore()
    settings = store.load()
    if listen_port is not None:
        try:
            port = int(listen_port)
        except (TypeError, ValueError) as error:
            raise ValueError(f"listen_port is not a number: {listen_port!r}") from error
        if not 1 <= port <= 65535:
            raise ValueError(f"listen_port out of range: {port}")
        settings = replace(settings, listen_port=port)
    if auto_sync_minutes is not None:
        try:
            minutes = int(auto_sync_minutes)
        except (TypeError, ValueError) as error:
            raise ValueError(f"auto_sync_minutes is not a number: {auto_sync_minutes!r}") from error
        if minutes < 0:
            raise ValueError(f"auto_sync_minutes must be >= 0: {minutes}")
        settings = replace(settings, auto_sync_minutes=minutes)
    store.update(settings)
    return settings


def _export_bot_sessions() -> None:
    """Chat-transcript bridge, outgoing half: the Bot's conversations become session
    files before a cycle, so the negotiation carries them (Android PRD v3.0)."""
    try:
        from hermes_cli.sync_sessions_bridge import export_bot_sessions
        from hermes_constants import get_hermes_home

        export_bot_sessions(get_hermes_home())
    except Exception as error:  # health: allow BLE001 -- the bridge is best-effort: a broken state.db must never block the sync cycle, and the skip is logged
        logging.getLogger(__name__).debug("session export skipped: %s", error)


def _import_synced_sessions() -> None:
    """Chat-transcript bridge, incoming half: session files a negotiation landed become
    part of the Bot's conversation history (source ``kourichat-sync``)."""
    try:
        from hermes_cli.sync_sessions_bridge import import_synced_sessions
        from hermes_constants import get_hermes_home

        import_synced_sessions(get_hermes_home())
    except Exception as error:  # health: allow BLE001 -- same best-effort seam: imported transcripts must never break the negotiation that carried them
        logging.getLogger(__name__).debug("session import skipped: %s", error)


def install_exit_sync() -> None:
    """Register the final-sync-atexit for long-lived hosts (the desktop backend, the
    web server): when the process leaves, try to find the chain and sync once."""
    import atexit

    atexit.register(_exit_sync_once)


def _exit_sync_once() -> None:
    try:
        outcome = _manager().sync_on_exit()
        if outcome is not None:
            logging.getLogger(__name__).info("exit sync done: role=%s records=%s", outcome.role, outcome.records)
    except Exception as error:  # health: allow BLE001 -- an exit path must never raise; the final sync is best-effort by contract
        logging.getLogger(__name__).debug("exit sync skipped: %s", error)


# Per-home manager singletons for long-lived hosts (web server, TUI backend): the
# master-role server started through one call is the same object a later status/stop
# addresses. Short-lived CLI paths keep _manager() fresh instances.
_MANAGERS: dict[str, SyncManager] = {}


def shared_manager() -> SyncManager:
    """The manager bound to the caller's profile home (resolves the active scope)."""
    from hermes_constants import get_hermes_home

    home = str(get_hermes_home())
    manager = _MANAGERS.get(home)
    if manager is None:
        manager = _manager()
        _MANAGERS[home] = manager
    return manager


# ── handlers ─────────────────────────────────────────────────────────────────────


def _cmd_pair(args) -> int:
    qr_payload = getattr(args, "qr", None)
    words_arg = getattr(args, "words", None)
    if qr_payload:
        from sync.qr import parse

        pairing = parse(qr_payload)
        if pairing is None:
            print("  ✗ not a kourichat-sync:v1 pairing payload (kourichat-sync:v1|words|host|port)")
            return 1
        entered, endpoint = pairing.words, f"{pairing.host}:{pairing.port}"
    elif words_arg:
        entered = words_arg.replace("\n", " ").split()
        endpoint = args.endpoint.strip() if args.endpoint else None
    else:
        print("  Enter the 24 words shown on the master device (space separated):")
        entered = input("  > ").strip().split()
        endpoint = args.endpoint.strip() if args.endpoint else None
    if len(entered) != 24:
        print(f"  ✗ expected 24 words, got {len(entered)}")
        return 1
    if endpoint is None:
        # No address: scan the LAN like the Android client (同 01) — masters of other
        # chains fail the challenge-response and are skipped by the candidate loop.
        from sync.discovery import zeroconf_available

        if not zeroconf_available():
            print("  ! LAN discovery needs the optional 'zeroconf' package:")
            print("      uv pip install zeroconf   (in this runtime's environment)")
        print("  no address given — scanning the LAN for a KouriChat Link master (up to 8s)...")
    else:
        parse_endpoint(endpoint)  # fail fast on a malformed endpoint before any state changes
    manager = _manager()
    state = manager.join_chain(entered, endpoint)
    print(f"  ✓ paired as secondary (device {state.device_id})")
    # The adopted master only exists in settings: a discovered endpoint is written back
    # during the successful negotiation, never into the state file.
    print(f"    master: {manager.status().master_endpoint}")
    print("    Run 'hermes sync now' to pull, 'hermes sync watch' to keep pulling.")
    return 0


def _cmd_create_chain() -> int:
    manager = _manager()
    words = manager.create_chain()
    print("  ✓ this device is now the chain master. The 24-word mnemonic below is the")
    print("    ONLY credential for the whole chain — write it down (paper), it cannot")
    print("    be recovered if lost, and anyone holding it can join the chain.")
    print()
    for i in range(0, 24, 6):
        print("    " + " ".join(words[i:i + 6]))
    print()
    print("  Run 'hermes sync serve' (or 'hermes sync watch') to accept connections,")
    print("  then pair the Android client with these words.")
    return 0


def _cmd_now() -> int:
    manager = _manager()
    outcome = manager.sync_now()
    if outcome.role == ROLE_MASTER:
        print(f"  ✓ record log refreshed ({outcome.records} syncable files)")
    else:
        print(f"  ✓ pulled {outcome.records} record(s), {outcome.applied} applied")
    return 0


def _cmd_status() -> int:
    status = _manager().status()
    print(f"  role:              {status.role}")
    print(f"  paired:            {'yes' if status.paired else 'no'}")
    if status.paired:
        print(f"  device id:         {status.device_id}")
    if status.role == ROLE_SECONDARY:
        print(f"  master endpoint:   {status.master_endpoint or '-'}")
        print(f"  pinned master TLS: {status.pinned_fingerprint or '(pins on first sync)'}")
    if status.role == ROLE_MASTER:
        print(f"  listen port:       {status.listen_port}")
    print(f"  tracked records:   {status.tracked_files}")
    print(f"  syncable files:    {status.syncable_files}")
    print(f"  applied / archived:{status.applied_count} / {status.archived_count}")
    print(f"  auto sync:         {status.auto_sync_minutes or 'off (manual)'} min")
    if status.last_sync_at_ms:
        import datetime

        when = datetime.datetime.fromtimestamp(status.last_sync_at_ms / 1000, datetime.UTC)
        print(f"  last sync:         {when.isoformat(timespec='seconds')} (UTC)")
    if status.last_sync_error:
        print(f"  last error:        {status.last_sync_error}")
    return 0


def _cmd_serve() -> int:
    manager = _manager()
    if manager.status().role != ROLE_MASTER:
        print("  ✗ only the chain master can serve; pair or create a chain first")
        return 1
    manager.sync_now()  # fresh record log before the first client connects
    server = manager.start_server()
    port = server.bound_port
    print(f"  ✓ KouriChat Link master listening on port {port} (all interfaces, LAN only)")
    print("    Ctrl-C to stop.")
    stop = threading.Event()
    try:
        stop.wait()
    except KeyboardInterrupt:
        pass
    finally:
        manager.stop_server()
        manager.sync_on_exit()  # master's exit sync: fold local edits into the record log
        print("  server stopped")
    return 0

def _cmd_watch() -> int:
    manager = _manager()
    settings = manager.status()
    if not settings.paired:
        print("  ✗ not paired; run 'hermes sync pair' or 'hermes sync create-chain' first")
        return 1
    if not settings.auto_sync_minutes:
        print("  ✗ auto sync is off; set kourichat_link.auto_sync_minutes in config.yaml")
        return 1
    print(f"  syncing every {settings.auto_sync_minutes} min (Ctrl-C to stop)")
    scheduler = SyncScheduler(manager, settings.auto_sync_minutes, on_error=_print_sync_error)
    try:
        scheduler.run_forever()
    except KeyboardInterrupt:
        pass
    # One last negotiation on the way out (discovery falls back when the master moved).
    outcome = manager.sync_on_exit()
    if outcome is not None:
        print(f"  ✓ exit sync done ({outcome.role})")
    print("  watch stopped")
    return 0


def _cmd_disconnect() -> int:
    _manager().disconnect_and_clear()
    print("  ✓ left the chain. Synced files were kept.")
    return 0


def _cmd_reset_chain(args) -> int:
    if not getattr(args, "yes", False):
        print("  This kills the chain for EVERY device and discards this device's pairing.")
        answer = input("  Type 'reset' to continue: ").strip().lower()
        if answer != "reset":
            print("  aborted")
            return 1
    _manager().reset_chain()
    print("  ✓ chain reset on this device. Synced files were kept.")
    return 0


def _print_sync_error(error: SyncError) -> None:
    import datetime

    when = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"  [{when}] sync cycle failed ({error.code.value}); will retry")
