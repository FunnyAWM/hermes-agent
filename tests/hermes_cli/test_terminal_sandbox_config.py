"""terminal.sandbox: shipped on with three presets, and the user's value reaches the CLI loader and the gate.

The gate reads the active profile's raw config through ``local_command_sandbox.settings()``, so the
assertion goes through the loader the CLI session uses and then through the reader that decides
confinement — a merged-dict check alone would not prove the switch does anything.
"""

import os
from unittest.mock import patch


def test_enabled_sandbox_survives_the_cli_loader_and_arms_the_gate(tmp_path, monkeypatch):
    from hermes_cli.config_defaults import DEFAULT_CONFIG

    sandbox = DEFAULT_CONFIG["terminal"]["sandbox"]
    assert sandbox["enabled"] is True
    assert (sandbox["binary"], sandbox["backend"], sandbox["allow_degraded"]) == ("sandctl", "l1", True)
    assert sandbox["profile"] == "workspace-exec"
    assert {name: entry["preset"] for name, entry in sandbox["profiles"].items()} == {
        "read-only": "read-only",
        "workspace-write": "workspace-write",
        "workspace-exec": "workspace-exec",
        "full-access": "full-access",
    }
    assert sandbox["escalation"] is True

    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text("terminal:\n  sandbox:\n    enabled: true\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))

    import cli

    # cli binds ``_hermes_home`` at import time; point this process's loader at the temp home.
    monkeypatch.setattr(cli, "_hermes_home", home, raising=False)
    with patch.dict(os.environ):  # load_cli_config bridges terminal values into the process env
        cli_config = cli.load_cli_config()

    assert cli_config["terminal"]["sandbox"]["enabled"] is True

    from tools.environments import local_command_sandbox

    assert local_command_sandbox.settings().enabled is True
