"""KouriChat Link defaults, held outside ``config_defaults.py``.

``config_defaults.py`` sits over its FILE_LINES ratchet and may only shrink, so a new
section cannot grow it; this pure-data sibling keeps the section's shipped defaults next
to the config system without pushing that file further over. ``sync_cmd`` reads these as
the fallbacks for the ``kourichat_link`` config.yaml section.
"""

KOURICHAT_LINK_DEFAULTS = {
    "role": "disabled",          # disabled | master | secondary (set by `hermes sync`)
    "master_endpoint": "",       # secondary role: the master device's host:port
    "listen_port": 27890,        # master role: TLS sync port, bound on all interfaces
    # Periodic sync for `hermes sync watch` / long-lived hosts; 0 = manual only.
    "auto_sync_minutes": 0,
}
