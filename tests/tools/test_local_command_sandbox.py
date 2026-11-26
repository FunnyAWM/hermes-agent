"""Contract tests for the opt-in sandctl command gate (tests/tools).

Pure string/policy behaviour is asserted on every host; the Windows-only pieces are marked so
they never fake ``sys.platform`` (root tests/AGENTS.md).
"""

import base64
import os
from pathlib import Path

import pytest

from tools.environments import local_command_sandbox as gate


@pytest.fixture()
def hermes_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


def _write_config(home: Path, body: str) -> None:
    (home / "config.yaml").write_text(body, encoding="utf-8")


def test_settings_default_follows_launcher_presence(hermes_home, monkeypatch):
    """On by default on an install that ships a launcher; never fails closed on a host
    where nothing could run the boundary."""
    _write_config(hermes_home, "terminal:\n  backend: local\n")
    monkeypatch.setattr(gate, "_launcher_present", lambda: False)
    cfg = gate.settings()
    assert cfg.enabled is False
    assert cfg.binary == "sandctl"
    assert cfg.backend == "l1"
    assert gate.active() is False
    monkeypatch.setattr(gate, "_launcher_present", lambda: True)
    assert gate.settings().enabled is True


def test_default_profiles_are_the_three_presets_and_active_is_workspace_exec(hermes_home):
    _write_config(hermes_home, "terminal:\n  sandbox: {}\n")
    cfg = gate.settings()
    assert set(cfg.profiles or {}) == {gate.READ_ONLY, gate.WORKSPACE_WRITE, gate.WORKSPACE_EXEC, gate.FULL_ACCESS}
    assert cfg.profile == gate.WORKSPACE_EXEC
    assert gate.active_profile(cfg).preset == gate.WORKSPACE_EXEC


def test_unknown_preset_falls_back_to_read_only(hermes_home):
    _write_config(
        hermes_home,
        "terminal:\n  sandbox:\n    profile: mine\n    profiles:\n      mine:\n        preset: bogus\n",
    )
    assert gate.active_profile(gate.settings()).preset == gate.READ_ONLY


def test_unknown_active_profile_falls_back_to_workspace_exec(hermes_home):
    _write_config(hermes_home, "terminal:\n  sandbox:\n    profile: nope\n")
    assert gate.settings().profile == gate.WORKSPACE_EXEC


def test_settings_read_from_config(hermes_home):
    _write_config(
        hermes_home,
        "terminal:\n  sandbox:\n    enabled: true\n    binary: /opt/sandctl\n    backend: l2\n"
        "    allow_degraded: false\n",
    )
    cfg = gate.settings()
    assert (cfg.enabled, cfg.binary, cfg.backend, cfg.allow_degraded) == (True, "/opt/sandctl", "l2", False)


def test_full_access_bypass_turns_the_gate_off(hermes_home, monkeypatch):
    _write_config(hermes_home, "terminal:\n  sandbox:\n    enabled: true\n")
    monkeypatch.setattr("tools.approval.is_approval_bypass_active", lambda: True)
    assert gate.settings().enabled is True
    assert gate.active() is False


def test_policy_grants_cwd_and_scratch_and_denies_network(tmp_path):
    cwd = tmp_path / "work"
    scratch = tmp_path / "scratch"
    cwd.mkdir()
    scratch.mkdir()
    shell = gate.SandboxShell("posix", "/bin/bash")
    policy = gate.build_policy(cwd=str(cwd), scratch=str(scratch), shell=shell)
    fs = policy["spec"]["fs"]
    assert gate._glob(cwd) in fs["read"]
    assert gate._glob(scratch) in fs["read"]
    assert gate._glob(cwd) in fs["write"]
    assert policy["spec"]["net"] == {"default": "deny"}


def _profile(preset: str, **kwargs) -> gate.SandboxProfile:
    return gate.SandboxProfile("p", preset, **kwargs)


def test_read_only_grants_no_writes(tmp_path):
    cwd = tmp_path / "work"
    cwd.mkdir()
    policy = gate.build_policy(
        cwd=str(cwd), scratch=str(tmp_path / "scratch"),
        shell=gate.SandboxShell("posix", "/bin/bash"), profile=_profile(gate.READ_ONLY))
    fs = policy["spec"]["fs"]
    assert fs["write"] == []
    assert gate._glob(cwd) in fs["read"]


def test_workspace_exec_adds_existing_build_caches(tmp_path, monkeypatch):
    cache = tmp_path / "npm-cache"
    cache.mkdir()
    monkeypatch.setattr(gate, "_build_cache_dirs", lambda: [cache])
    policy = gate.build_policy(
        cwd=str(tmp_path), scratch=str(tmp_path / "scratch"),
        shell=gate.SandboxShell("posix", "/bin/bash"), profile=_profile(gate.WORKSPACE_EXEC))
    assert gate._glob(cache) in policy["spec"]["fs"]["write"]


def test_custom_paths_are_added_and_missing_ones_dropped(tmp_path):
    extra = tmp_path / "shared"
    extra.mkdir()
    profile = gate.SandboxProfile(
        "p", gate.CUSTOM, read=(str(extra),), write=(str(extra), str(tmp_path / "absent")))
    policy = gate.build_policy(
        cwd=str(tmp_path), scratch=str(tmp_path / "scratch"),
        shell=gate.SandboxShell("posix", "/bin/bash"), profile=profile)
    fs = policy["spec"]["fs"]
    assert gate._glob(extra) in fs["read"] and gate._glob(extra) in fs["write"]
    assert not any("absent" in entry for entry in fs["write"])


def test_read_only_ignores_configured_write_paths(tmp_path):
    """Read only means read only: an extra write path cannot widen it."""
    extra = tmp_path / "shared"
    extra.mkdir()
    profile = gate.SandboxProfile("p", gate.READ_ONLY, write=(str(extra),))
    policy = gate.build_policy(
        cwd=str(tmp_path), scratch=str(tmp_path / "scratch"),
        shell=gate.SandboxShell("posix", "/bin/bash"), profile=profile)
    assert policy["spec"]["fs"]["write"] == []


def test_read_only_profile_refuses_file_tool_writes(hermes_home, tmp_path):
    """The file tools run in-process, outside the boundary, so they must refuse too."""
    _write_config(
        hermes_home,
        "terminal:\n  sandbox:\n    enabled: true\n    profile: read-only\n"
        "    profiles:\n      read-only:\n        preset: read-only\n",
    )
    from agent.file_safety import get_write_denied_error

    assert gate.file_writes_read_only() is True
    message = get_write_denied_error(str(tmp_path / "notes.txt")) or ""
    assert "read-only" in message and "Read only" in message


def test_policy_path_is_isolated_per_profile(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.environments.base.get_sandbox_dir", lambda: tmp_path / "sandboxes")
    one = gate.policy_path(cwd=str(tmp_path), scratch=str(tmp_path), backend="l1", profile="a")
    two = gate.policy_path(cwd=str(tmp_path), scratch=str(tmp_path), backend="l1", profile="b")
    assert one != two


def test_deny_entry_dropped_when_the_path_is_absent(tmp_path):
    """An absent deny is a no-op the backend would warn about on every run."""
    missing = tmp_path / "missing" / "config.json"
    assert gate._deny_entry_usable(str(missing)) is False
    assert gate._deny_entry_usable(f"{missing.parent}/**") is False


def test_deny_entry_kept_when_it_exists_or_a_write_grant_covers_it(tmp_path):
    existing = tmp_path / "credentials"
    existing.mkdir()
    assert gate._deny_entry_usable(f"{existing}/**") is True
    cwd = tmp_path / "work"
    cwd.mkdir()
    assert gate._deny_entry_usable(f"{cwd / '.ssh'}/**", (cwd,)) is True


def test_policy_denies_only_paths_that_matter(tmp_path):
    cwd = tmp_path / "work"
    scratch = tmp_path / "scratch"
    cwd.mkdir()
    scratch.mkdir()
    policy = gate.build_policy(cwd=str(cwd), scratch=str(scratch), shell=gate.SandboxShell("posix", "/bin/bash"))
    for entry in policy["spec"]["fs"]["deny"]:
        base = Path(entry[:-3] if entry.endswith(("/**", "\\**")) else entry)
        assert base.exists() or base.is_relative_to(cwd) or base.is_relative_to(scratch)


def test_powershell_script_pins_utf8_output(tmp_path):
    """5.1 writes in the console codepage while the host decodes UTF-8, so the wrapper must
    pin both its own and native-child output to UTF-8 (non-ASCII paths otherwise corrupt)."""
    run = gate.ConfinedRun(
        shell=gate.SandboxShell("powershell", "powershell.exe"),
        settings=gate.SandboxSettings(True, "sandctl", "l1", True),
        policy_path=tmp_path / "policy.yaml", cwd=str(tmp_path),
        rc_marker="__RC__", cwd_marker="__CWD__",
    )
    script = gate.powershell_script("Write-Output 'ok'", cwd=str(tmp_path), markers=run)
    assert "[Console]::OutputEncoding" in script
    assert "chcp 65001" in script


def test_make_run_publishes_the_policy_outside_the_grants(tmp_path, monkeypatch):
    sandboxes = tmp_path / "sandboxes"
    monkeypatch.setattr("tools.environments.base.get_sandbox_dir", lambda: sandboxes)
    cwd = tmp_path / "work"
    cwd.mkdir()
    run = gate.make_run(
        shell=gate.SandboxShell("posix", "/bin/bash"),
        cfg=gate.SandboxSettings(True, "sandctl", "l1", True),
        cwd=str(cwd), scratch=str(tmp_path / "scratch"), cwd_marker="__HERMES_CWD_x__",
    )
    assert run.policy_path.is_file()
    assert sandboxes in run.policy_path.parents
    assert "__HERMES_CWD_x__" == run.cwd_marker


def test_powershell_script_without_markers_still_exits_with_the_status(tmp_path):
    """A background spawn has no finalizer, so it emits no markers — but the launcher's exit
    status must still be the command's own."""
    script = gate.powershell_script("Write-Output 'ok'", cwd=str(tmp_path), markers=None)
    assert "exit $__hermes_rc" in script
    assert "Write-Output ('" not in script


def test_background_command_wraps_the_command_in_the_launcher(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "settings", lambda: gate.SandboxSettings(True, "sandctl", "l1", True))
    monkeypatch.setattr(gate, "resolve_binary", lambda cfg: "sandctl")
    monkeypatch.setattr(gate, "confined_shell", lambda: gate.SandboxShell("posix", "/bin/bash"))
    monkeypatch.setattr("tools.environments.base.get_sandbox_dir", lambda: tmp_path / "sandboxes")
    command = gate.background_command(command="echo hi", cwd=str(tmp_path), scratch=str(tmp_path))
    assert command.startswith("sandctl run")
    assert "/bin/bash" in command and "echo hi" in command


def test_background_command_fails_closed_without_a_launcher(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "settings", lambda: gate.SandboxSettings(True, "sandctl", "l1", True))
    monkeypatch.setattr(gate, "resolve_binary", lambda cfg: None)
    with pytest.raises(RuntimeError, match="launcher is not installed"):
        gate.background_command(command="echo hi", cwd=str(tmp_path), scratch=str(tmp_path))


def test_posix_shell_argv_keeps_bash():
    shell = gate.SandboxShell("posix", "/bin/bash")
    assert gate.shell_argv(shell, "echo hi", login=True) == ["/bin/bash", "-l", "-c", "echo hi"]


def test_powershell_shell_argv_encodes_the_script():
    shell = gate.SandboxShell("powershell", r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
    argv = gate.shell_argv(shell, "Write-Output hi")
    assert argv[0] == shell.path
    assert argv[argv.index("-EncodedCommand") + 1] == base64.b64encode(
        "Write-Output hi".encode("utf-16-le")).decode("ascii")


def _run(shell_kind="powershell"):
    return gate.ConfinedRun(
        shell=gate.SandboxShell(shell_kind, "shell"),
        settings=gate.SandboxSettings(True, "sandctl", "l1", True),
        policy_path=Path("policy.yaml"), cwd="cwd",
        rc_marker="__RC__", cwd_marker="__CWD__",
    )


def test_finalize_uses_the_wrapper_exit_status():
    run = _run()
    result = gate.finalize({"output": "hello\n__CWD__/w__CWD__\n__RC__0__RC__\n", "returncode": 1}, run)
    assert result["returncode"] == 0
    assert "__RC__" not in result["output"]


def test_finalize_recovers_the_status_of_a_command_that_called_exit():
    run = _run()
    result = gate.finalize({"output": "sandctl: the command exited with code 7\n", "returncode": 1}, run)
    assert result["returncode"] == 7


def test_finalize_explains_a_refused_run():
    run = _run()
    result = gate.finalize({"output": "[FAIL] the command was blocked at the boundary\n", "returncode": gate.BLOCKED_EXIT}, run)
    assert result["output"].startswith("[sandbox] The command sandbox blocked this command")
    assert result["returncode"] == gate.BLOCKED_EXIT


def test_finalize_decodes_clixml_errors_and_drops_boilerplate():
    run = _run()
    output = (
        "#< CLIXML\n<Objs Version=\"1.1\"><S S=\"Error\">denied_x000D__x000A_</S></Objs>"
        "sandctl: limitation: read allowlist is advisory\n"
    )
    result = gate.finalize({"output": output, "returncode": 1}, run)
    assert result["output"].strip() == "denied"
    assert "sandctl: limitation:" not in result["output"]
    assert "#< CLIXML" not in result["output"]


@pytest.mark.platforms("windows")
def test_windows_resolves_a_powershell_shell():
    shell = gate.confined_shell()
    assert shell is not None and shell.kind == "powershell"
    assert "powershell" in shell.path.lower()


def test_resolve_binary_prefers_an_explicit_path(tmp_path):
    exe = tmp_path / "sandctl"
    exe.write_text("", encoding="utf-8")
    assert gate.resolve_binary(gate.SandboxSettings(True, str(exe), "l1", True)) == str(exe)
    assert gate.resolve_binary(gate.SandboxSettings(True, str(tmp_path / "absent"), "l1", True)) is None


def test_resolve_binary_falls_back_to_the_bundled_copy(tmp_path, monkeypatch):
    """The Desktop app ships sandctl: a bare `sandctl` that is not on PATH resolves to the
    copy beside the app rather than failing closed."""
    bundled = tmp_path / "sandctl"
    bundled.write_text("", encoding="utf-8")
    monkeypatch.setattr(gate.shutil, "which", lambda _name: None)
    monkeypatch.setattr(gate, "_bundled_binary", lambda: bundled)
    assert gate.resolve_binary(gate.SandboxSettings(True, "sandctl", "l1", True)) == str(bundled)
    # An explicitly named alternative is never silently replaced by the bundled copy.
    assert gate.resolve_binary(gate.SandboxSettings(True, "other-launcher", "l1", True)) is None


def test_bundled_binary_looks_beside_the_package(tmp_path, monkeypatch):
    """A checkout keeps it in vendor/sandctl; the packaged app in resources/sandctl."""
    exe = "sandctl.exe" if gate._IS_WINDOWS else "sandctl"
    module = tmp_path / "repo" / "tools" / "environments" / "local_command_sandbox.py"
    module.parent.mkdir(parents=True)
    module.write_text("", encoding="utf-8")
    monkeypatch.setattr(gate, "__file__", str(module))
    assert gate._bundled_binary() is None
    vendored = tmp_path / "repo" / "vendor" / "sandctl" / exe
    vendored.parent.mkdir(parents=True)
    vendored.write_text("", encoding="utf-8")
    assert gate._bundled_binary() == vendored


def test_missing_binary_message_names_the_config_key(monkeypatch):
    monkeypatch.setattr("tools.environments.local_command_sandbox.display_hermes_home", lambda: "~/.hermes")
    message = gate.missing_binary_message(gate.SandboxSettings(True, "sandctl", "l1", True))
    assert "terminal.sandbox.binary" in message


def test_profile_reads_backend_and_policy_overlay(hermes_home):
    """A custom profile may pin its own isolation level and declare sandctl spec fields."""
    _write_config(
        hermes_home,
        "terminal:\n  sandbox:\n    enabled: true\n    backend: l1\n    profile: mine\n"
        "    profiles:\n      mine:\n        preset: custom\n        backend: l2\n"
        "        policy:\n          syscall: {profile: strict}\n"
        "          net: {mode: allowlist, allow: ['pypi.org:443']}\n",
    )
    cfg = gate.settings()
    profile = gate.active_profile(cfg)
    assert profile.backend == "l2"
    assert profile.policy == {
        "syscall": {"profile": "strict"},
        "net": {"mode": "allowlist", "allow": ["pypi.org:443"]},
    }


def test_policy_overlay_replaces_scalars_and_unions_lists(tmp_path):
    cwd = tmp_path / "work"
    cwd.mkdir()
    extra = tmp_path / "shared"
    extra.mkdir()
    profile = gate.SandboxProfile(
        "p", gate.CUSTOM, read=(str(extra),),
        policy={
            "fs": {"deny": [gate._glob(tmp_path / "secrets")]},
            "net": {"mode": "allowlist", "allow": ["pypi.org:443"], "allowCidr": ["10.0.0.0/8:443"]},
            "syscall": {"profile": "restricted"},
            "proc": {"cpu": "2", "pids": 256, "noNewPrivs": True},
            "secrets": {"inject": False},
        })
    policy = gate.build_policy(
        cwd=str(cwd), scratch=str(tmp_path / "scratch"),
        shell=gate.SandboxShell("posix", "/bin/bash"), profile=profile)
    spec = policy["spec"]
    assert spec["net"]["mode"] == "allowlist"
    assert spec["net"]["default"] == "deny"
    assert spec["net"]["allow"] == ["pypi.org:443"]
    assert spec["syscall"] == {"profile": "restricted"}
    assert spec["proc"] == {"cpu": "2", "pids": 256, "noNewPrivs": True}
    assert spec["secrets"] == {"inject": False}
    # The generated grants survive; the overlay only adds.
    assert gate._glob(cwd) in spec["fs"]["read"]
    assert gate._glob(extra) in spec["fs"]["read"]
    assert gate._glob(tmp_path / "secrets") in spec["fs"]["deny"]


def test_policy_overlay_cannot_drop_baseline_denies(tmp_path, monkeypatch):
    cwd = tmp_path / "work"
    cwd.mkdir()
    secrets = tmp_path / "credentials"
    secrets.mkdir()
    monkeypatch.setattr(gate, "_DENY_GLOBS", (f"{secrets}{os.sep}**",))
    profile = gate.SandboxProfile("p", gate.WORKSPACE_WRITE, policy={"fs": {"deny": []}})
    policy = gate.build_policy(
        cwd=str(cwd), scratch=str(tmp_path / "scratch"),
        shell=gate.SandboxShell("posix", "/bin/bash"), profile=profile)
    assert gate._glob(secrets) in policy["spec"]["fs"]["deny"]


def test_policy_overlay_ignores_unknown_spec_keys(tmp_path):
    cwd = tmp_path / "work"
    cwd.mkdir()
    profile = gate.SandboxProfile("p", gate.WORKSPACE_WRITE, policy={"nonsense": {"x": 1}})
    policy = gate.build_policy(
        cwd=str(cwd), scratch=str(tmp_path / "scratch"),
        shell=gate.SandboxShell("posix", "/bin/bash"), profile=profile)
    assert "nonsense" not in policy["spec"]


def test_profile_backend_overrides_the_global_backend_in_argv(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.environments.base.get_sandbox_dir", lambda: tmp_path / "sandboxes")
    cwd = tmp_path / "work"
    cwd.mkdir()
    cfg = gate.SandboxSettings(
        True, "sandctl", "l1", True, profile="mine",
        profiles={"mine": gate.SandboxProfile("mine", gate.WORKSPACE_WRITE, backend="l3")})
    run = gate.make_run(shell=gate.SandboxShell("posix", "/bin/bash"), cfg=cfg,
                        cwd=str(cwd), scratch=str(tmp_path / "scratch"))
    assert run.backend == "l3"
    argv = gate.sandbox_argv(cfg, "sandctl", run, "echo hi")
    assert argv[argv.index("--backend") + 1] == "l3"


def test_read_only_profile_ignores_a_filesystem_policy_override(tmp_path):
    """Read only promises no writes: a policy fragment may add net/caps, never fs grants."""
    cwd = tmp_path / "work"
    cwd.mkdir()
    extra = tmp_path / "shared"
    extra.mkdir()
    profile = gate.SandboxProfile(
        "p", gate.READ_ONLY,
        policy={"fs": {"mode": "denylist", "write": [gate._glob(extra)]},
                "net": {"allowCidr": ["10.0.0.0/8:443"]}})
    policy = gate.build_policy(
        cwd=str(cwd), scratch=str(tmp_path / "scratch"),
        shell=gate.SandboxShell("posix", "/bin/bash"), profile=profile)
    assert policy["spec"]["fs"]["write"] == []
    assert "mode" not in policy["spec"]["fs"]
    assert gate._glob(extra) not in policy["spec"]["fs"]["read"]
    assert policy["spec"]["net"]["allowCidr"] == ["10.0.0.0/8:443"]
