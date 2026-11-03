"""Passive opt-out keeps explicit update checks available."""
import subprocess

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
