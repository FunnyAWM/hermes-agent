"""``hermes sync`` subcommand parser."""

from __future__ import annotations

from typing import Callable


def build_sync_parser(subparsers, *, cmd_sync: Callable) -> None:
    """Attach the ``sync`` subcommand group to ``subparsers``."""
    parser = subparsers.add_parser(
        "sync", help="KouriChat Link device-chain sync (pair with the Android client)",
        description="Pair this device into a KouriChat Link chain and sync whitelisted "
                    "memory files with the Android client over LAN")
    sync_sub = parser.add_subparsers(dest="sync_action")

    pair_parser = sync_sub.add_parser("pair", help="Join a chain as secondary (two-way mirror)")
    pair_parser.add_argument("endpoint", metavar="host:port", nargs="?",
                             help="Master device endpoint, e.g. 192.168.1.20:27890; omit to scan the LAN")
    pair_parser.add_argument("--words", help="The 24-word mnemonic; omit to enter interactively")
    pair_parser.add_argument(
        "--qr", dest="qr",
        help="A scanned pairing payload (kourichat-sync:v1|words|host|port) instead of endpoint+words")

    sync_sub.add_parser("create-chain", help="Create a chain: this device becomes the master")

    sync_sub.add_parser("now", help="Sync once (refresh the log as master, pull as secondary)")
    sync_sub.add_parser("status", help="Show pairing and sync state")
    sync_sub.add_parser("serve", help="Run the master sync server in the foreground")
    sync_sub.add_parser("watch", help="Sync periodically (kourichat_link.auto_sync_minutes)")
    sync_sub.add_parser("disconnect", help="Leave the chain (secondary); synced files stay")

    reset_parser = sync_sub.add_parser("reset-chain", help="Kill the chain (master); discards pairing")
    reset_parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")

    parser.set_defaults(func=cmd_sync)
