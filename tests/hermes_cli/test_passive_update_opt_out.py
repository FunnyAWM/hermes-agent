"""Passive opt-out keeps explicit update checks available."""
import asyncio
import json
import subprocess

import pytest

from hermes_constants import get_hermes_home


def _checkout_with_ahead_origin(tmp_path):
    remote = tmp_path / "remote"
    local = tmp_path / "checkout"

    def git(*args):
        return subprocess.run(["git", *map(str, args)], check=True, capture_output=True, text=True)

    git("init", "-b", "main", remote)
    git("-C", remote, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "--allow-empty", "-m", "initial")
    git("clone", remote, local)
    git("-C", remote, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "--allow-empty", "-m", "next")
    return local


def test_default_config_ships_passive_checks_off():
    """T-11: the shipped default must not opt a fresh profile into update traffic."""
    from hermes_cli.config_defaults import DEFAULT_CONFIG

    assert DEFAULT_CONFIG["updates"]["check"] is False


def test_passive_check_is_disabled_unless_the_user_opts_in(tmp_path):
    """PRD §8.1 隐 02: a fresh PC profile's only outbound link is the model endpoint."""
    from hermes_cli.source_check import check_for_updates

    local = _checkout_with_ahead_origin(tmp_path)
    home = get_hermes_home()
    assert check_for_updates(passive=True, install_root=local, home=home)["reason"] == "disabled"
    # The explicit path still works and still sees the extra commit on origin.
    status = check_for_updates(install_root=local, home=home, force=True)
    assert status["updateAvailable"] is True
    assert status["behind"] in (-1, 1)


def test_explicit_check_fetches_local_origin_despite_passive_opt_out(tmp_path, monkeypatch, capsys):
    from hermes_cli import main
    from hermes_cli.update_cmd import _cmd_update_check

    local = _checkout_with_ahead_origin(tmp_path)
    monkeypatch.setattr(main, "PROJECT_ROOT", local)
    (get_hermes_home() / "config.yaml").write_text("updates:\n  check: false\n", encoding="utf-8")
    _cmd_update_check()
    output = capsys.readouterr().out
    assert "Fetching from origin" in output
    assert "1 commit" in output


@pytest.mark.parametrize("setting", [None, False, True])
def test_desktop_probe_requires_opt_in_or_a_forced_check(tmp_path, monkeypatch, capsys, setting):
    from hermes_cli.source_check import main

    local = _checkout_with_ahead_origin(tmp_path)
    home = get_hermes_home()
    if setting is not None:
        (home / "config.yaml").write_text(f"updates:\n  check: {str(setting).lower()}\n", encoding="utf-8")
    argv = ["source_check", "--install-root", str(local), "--home", str(home)]
    monkeypatch.setattr("sys.argv", argv)
    main()
    passive = json.loads(capsys.readouterr().out)
    if setting is True:
        assert passive["updateAvailable"] is True
    else:
        assert passive["reason"] == "disabled"
        assert not (home / "source-checks").exists()

    monkeypatch.setattr("sys.argv", [*argv, "--force"])
    main()
    explicit = json.loads(capsys.readouterr().out)
    assert explicit["updateAvailable"] is True


@pytest.mark.parametrize("setting", [None, False, True])
def test_dashboard_poll_requires_opt_in_or_a_forced_check(tmp_path, monkeypatch, setting):
    from hermes_cli import config, web_server
    from hermes_cli.web_routers import actions

    local = _checkout_with_ahead_origin(tmp_path)
    home = get_hermes_home()
    if setting is not None:
        (home / "config.yaml").write_text(f"updates:\n  check: {str(setting).lower()}\n", encoding="utf-8")
    monkeypatch.setattr(web_server, "PROJECT_ROOT", local)
    monkeypatch.setattr(config, "get_project_root", lambda: local)

    passive = asyncio.run(actions.check_hermes_update())
    if setting is True:
        assert passive["update_available"] is True
    else:
        assert passive["update_available"] is False
        assert passive["behind"] is None
        assert passive["reason"] == "disabled"
        assert passive["message"] == "Automatic update checks are disabled."
        assert not (home / "source-checks").exists()

    explicit = asyncio.run(actions.check_hermes_update(force=True))
    assert explicit["update_available"] is True
