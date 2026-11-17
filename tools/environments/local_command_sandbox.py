"""Opt-in sandctl boundary around local (host) command execution.

Every command the agent runs on the host goes through ``sandctl run`` unless the
session has Full Access (``tools.approval.is_approval_bypass_active``) or the operator
left ``terminal.sandbox.enabled`` off. The boundary is fixed at the execution layer
(``tools.environments.local.LocalEnvironment``), never chosen by the model.

Windows cannot run the msys bash inside the sandbox's AppContainer (msys-2.0 writes
\\BaseNamedObjects and is refused; see SESSION_TODO §四 S-01), so a confined Windows
command runs under Windows PowerShell (5.1) instead, and the file tools take their native
in-process path instead of the POSIX shell one. POSIX hosts keep bash inside the sandbox.

The policy is generated per working directory and published under
``HERMES_HOME/sandboxes``, which must stay outside every grant it declares: sandctl
refuses a policy file its own rules would let the sandboxed command rewrite.
"""

from __future__ import annotations

import base64
import contextlib
import contextvars
import hashlib
import html
import logging
import os
import re
import shlex
import shutil
import sys
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import hermes_yaml as yaml

from hermes_cli.config import cfg_get, read_raw_config_readonly
from hermes_constants import display_hermes_home
from tools.environments.base_session_env import _cwd_marker

logger = logging.getLogger(__name__)

_IS_WINDOWS = sys.platform == "win32"

# sandctl exit codes that mean "the command never ran" (see `sandctl run --help`).
REFUSED_EXIT = 3
BLOCKED_EXIT = 4

# Credential locations the boundary always closes, even when a grant covers a parent.
_DENY_GLOBS = (
    "~/.ssh/**",
    "~/.aws/**",
    "~/.gnupg/**",
    "~/.docker/config.json",
    "~/.hermes/.env",
    "~/AppData/Roaming/Microsoft/Credentials/**",
    "~/AppData/Local/Microsoft/Credentials/**",
)


# Preset names double as the default profile names (Desktop shows translated titles).
READ_ONLY = "read-only"
WORKSPACE_WRITE = "workspace-write"
WORKSPACE_EXEC = "workspace-exec"
FULL_ACCESS = "full-access"
CUSTOM = "custom"
_PRESETS = (READ_ONLY, WORKSPACE_WRITE, WORKSPACE_EXEC, FULL_ACCESS, CUSTOM)

# Narrowest -> widest. Escalation may only move right; a request that moves left runs
# tighter without asking (a model asking for less access never needs the user's consent).
_RANK = {READ_ONLY: 0, WORKSPACE_WRITE: 1, WORKSPACE_EXEC: 2, FULL_ACCESS: 3}

# The level a single command asked for (terminal's ``sandbox`` argument). Set around one
# execute/spawn, inherited by the deadline worker through copy_context.
_ESCALATED: contextvars.ContextVar[SandboxProfile | None] = contextvars.ContextVar(
    "hermes_sandbox_escalation", default=None)


@dataclass(frozen=True)
class SandboxProfile:
    """One named boundary: a preset, its extra paths, and optional sandctl overrides.

    ``backend`` pins the isolation level for this profile (``l1``/``l2``/``l3`` or a
    concrete backend name); empty inherits ``terminal.sandbox.backend``. ``policy`` is a
    sandctl ``spec`` fragment merged over the generated policy, which is how a custom
    profile reaches the fields the presets do not model (network, syscall filter,
    resource caps, secret injection)."""

    name: str
    preset: str = WORKSPACE_WRITE
    read: tuple[str, ...] = ()
    write: tuple[str, ...] = ()
    backend: str = ""
    policy: dict[str, Any] | None = field(default=None, compare=False)


@dataclass(frozen=True)
class SandboxSettings:
    enabled: bool
    binary: str
    backend: str
    allow_degraded: bool
    profile: str = WORKSPACE_EXEC
    profiles: dict[str, SandboxProfile] | None = None
    # May the model ask, per command, for a wider level (the user is asked first)?
    escalation: bool = True


@dataclass(frozen=True)
class SandboxShell:
    """The shell a confined command runs in: ``kind`` is ``posix`` or ``powershell``."""

    kind: str
    path: str


@dataclass(frozen=True)
class ConfinedRun:
    """Everything one confined spawn needs; also the finalize() key on the process."""

    shell: SandboxShell
    settings: SandboxSettings
    policy_path: Path
    cwd: str
    rc_marker: str
    cwd_marker: str
    # Isolation level this run asks for (a profile may pin its own).
    backend: str = "l1"



def default_profiles() -> dict[str, SandboxProfile]:
    return {name: SandboxProfile(name, name)
            for name in (READ_ONLY, WORKSPACE_WRITE, WORKSPACE_EXEC, FULL_ACCESS)}


def _profile(name: str, raw: object) -> SandboxProfile:
    data = raw if isinstance(raw, dict) else {}
    preset = str(data.get("preset") or WORKSPACE_WRITE).strip().lower()
    if preset not in _PRESETS:
        # Never widen on a typo: an unknown preset falls back to the strictest one.
        logger.warning("terminal.sandbox.profiles.%s: unknown preset %r; using %s", name, preset, READ_ONLY)
        preset = READ_ONLY
    policy = data.get("policy")
    return SandboxProfile(
        name=name,
        preset=preset,
        read=tuple(str(p).strip() for p in data.get("read") or () if str(p).strip()),
        write=tuple(str(p).strip() for p in data.get("write") or () if str(p).strip()),
        backend=str(data.get("backend") or "").strip(),
        policy=policy if isinstance(policy, dict) else None,
    )


def _profiles(section: dict) -> dict[str, SandboxProfile]:
    raw = section.get("profiles")
    if not isinstance(raw, dict) or not raw:
        return default_profiles()
    return {str(name): _profile(str(name), value) for name, value in raw.items()}


def _launcher_present() -> bool:
    """Whether this install ships a launcher: the default is ON only when one exists."""
    return shutil.which("sandctl") is not None or _bundled_binary() is not None


def settings() -> SandboxSettings:
    """``terminal.sandbox`` from the active profile's config.yaml.

    On by default when a launcher is installed (PATH or the copy shipped beside the app),
    off on a host with none so a stock install never fails closed on every command."""
    cfg = read_raw_config_readonly()
    section = cfg_get(cfg, "terminal", "sandbox", default={}) or {}
    if not isinstance(section, dict):
        section = {}
    binary = str(section.get("binary") or "sandctl").strip() or "sandctl"
    backend = str(section.get("backend") or "l1").strip() or "l1"
    profiles = _profiles(section)
    name = str(section.get("profile") or WORKSPACE_EXEC).strip() or WORKSPACE_EXEC
    if name not in profiles:
        logger.warning("terminal.sandbox.profile %r is not in terminal.sandbox.profiles; using %r",
                       name, WORKSPACE_EXEC)
        name = WORKSPACE_EXEC if WORKSPACE_EXEC in profiles else next(iter(profiles))
    return SandboxSettings(
        enabled=section.get("enabled", _launcher_present()) is not False,
        binary=binary,
        backend=backend,
        allow_degraded=section.get("allow_degraded", True) is not False,
        profile=name,
        profiles=profiles,
        escalation=section.get("escalation", True) is not False,
    )


def active_profile(cfg: SandboxSettings | None = None) -> SandboxProfile:
    """The boundary in force: a one-command escalation wins over the configured profile."""
    override = _ESCALATED.get()
    if override is not None:
        return override
    cfg = cfg or settings()
    return (cfg.profiles or default_profiles()).get(cfg.profile, SandboxProfile(cfg.profile, WORKSPACE_EXEC))


def widening(level: str) -> bool:
    """Whether *level* is a wider boundary than the one in force right now."""
    return _RANK[level] > _RANK.get(active_profile().preset, 1)


@contextlib.contextmanager
def escalation(level: str):
    """Run one command at *level*; the configured profile applies again afterwards."""
    token = _ESCALATED.set(SandboxProfile(f"escalated:{level}", level))
    try:
        yield
    finally:
        _ESCALATED.reset(token)


def file_writes_read_only() -> bool:
    """True when the active profile grants no file writes: the file tools must refuse too.

    They run in-process (native on Windows under the gate), outside the sandbox, so the
    policy alone would leave them writing into a workspace the user made read-only."""
    if not active():
        return False
    return active_profile(settings()).preset == READ_ONLY


def _bypass_active() -> bool:
    try:
        from tools.approval import is_approval_bypass_active

        return bool(is_approval_bypass_active())
    except Exception:  # approval unavailable: stay confined rather than silently opening up
        logger.debug("approval bypass probe failed; treating the session as confined", exc_info=True)
        return False


def active() -> bool:
    """True when this command must be confined: enabled, not Full Access (session or profile),
    and not running under a one-command escalation to ``full-access``."""
    cfg = settings()
    if not cfg.enabled or _bypass_active():
        return False
    return active_profile(cfg).preset != FULL_ACCESS


def _bundled_binary() -> Path | None:
    """The sandctl shipped with the app (``vendor/sandctl`` in a checkout,
    ``resources/sandctl`` in the packaged Desktop app)."""
    exe = "sandctl.exe" if _IS_WINDOWS else "sandctl"
    here = Path(__file__).resolve()
    roots = [here.parents[2]]
    if len(here.parents) > 3:
        roots += [here.parents[3], here.parents[4]]
    for root in roots:
        for candidate in (root / "vendor" / "sandctl" / exe, root / "sandctl" / exe):
            if candidate.is_file():
                return candidate
    return None


def resolve_binary(cfg: SandboxSettings) -> str | None:
    """Absolute path to the sandctl executable, or None when it is not installed.

    Order: an explicit path, the configured name on PATH, then the bundled copy
    (default name only)."""
    candidate = cfg.binary
    if os.path.sep in candidate or (os.path.altsep and os.path.altsep in candidate):
        return candidate if Path(candidate).is_file() else None
    found = shutil.which(candidate)
    if found:
        return found
    if candidate == "sandctl":
        bundled = _bundled_binary()
        if bundled is not None:
            return str(bundled)
    return None


def _find_executable(names: tuple[str, ...]) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def confined_shell() -> SandboxShell | None:
    """The shell a confined command runs in, or None when the host has none.

    Windows: Windows PowerShell 5.1 (no pwsh: this gate targets stock Windows). POSIX:
    bash, which the sandbox can run.
    """
    if _IS_WINDOWS:
        path = _find_executable(("powershell.exe", "powershell"))
        return SandboxShell("powershell", path) if path else None
    path = _find_executable(("bash",))
    return SandboxShell("posix", path) if path else None


def file_tools_native() -> bool:
    """Whether the file tools must bypass the shell: a confined Windows host has no bash.

    The native path runs inside the agent process, NOT inside the boundary — an explicit
    limit of this slice (SESSION_TODO §四 plan item 3)."""
    return _IS_WINDOWS and active()


def _glob(path: Path) -> str:
    """``<path>\\**`` on Windows, ``<path>/**`` elsewhere (sandctl's drive-letter rule)."""
    text = str(Path(str(path))).rstrip("\\/") or str(path)
    return f"{text}\\**" if _IS_WINDOWS else f"{text}/**"


def _expand_deny(entry: str) -> str:
    """Expand a leading ``~/`` and use the host separator; everything else stays literal."""
    if not entry.startswith("~/"):
        return entry
    return str(Path.home() / entry[2:].replace("/", os.sep))


def _deny_entry_usable(entry: str, writable: tuple[Path, ...] = ()) -> bool:
    """Deny only paths that can matter: an absent path is a no-op, and the backend reports
    each such entry as a warning on every run. The exception is a missing path inside a
    write grant — the sandbox could create it later, so the deny must stay."""
    base = Path(entry[:-3]) if entry.endswith(("/**", "\\**")) else Path(entry)
    if base.exists():
        return True
    return any(base.is_relative_to(Path(w)) for w in writable)


def _interpreter_read_globs() -> list[str]:
    """Read grants for the agent's own interpreter and its venv.

    A venv ``python.exe`` on Windows is a trampoline into the real (often uv-managed)
    install; granting only PATH's ``.venv\\Scripts`` makes ``python`` die with an
    unresolvable-executable error that reads like the command's own bug."""
    # sys.base_prefix is the base install when running from a venv (the one PATH never names);
    # VIRTUAL_ENV can be unset (a host spawned without activation), so it is only a bonus.
    paths = {Path(sys.executable).parent, Path(sys.prefix), Path(sys.base_prefix),
             Path(os.path.realpath(sys.executable)).parent}
    venv = os.environ.get("VIRTUAL_ENV")
    if venv:
        paths.add(Path(venv))
        # pyvenv.cfg `home` is the base install the venv's `python.exe` really runs
        # (uv-managed, store-installed, …) — the one path PATH never names.
        try:
            for line in (Path(venv) / "pyvenv.cfg").read_text(encoding="utf-8-sig").splitlines():
                key, _, value = line.partition("=")
                if key.strip().lower() == "home" and value.strip():
                    paths.add(Path(value.strip()))
        except OSError:
            pass
    return [_glob(p) for p in paths if p.is_dir()]


def _toolchain_read_globs() -> list[str]:
    """Read grants for the directories on PATH: binaries the command may exec.

    Nothing else on the host is readable, so a tool the policy does not name fails at
    load time rather than silently running outside the boundary."""
    seen: dict[str, None] = {}
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        entry = entry.strip().strip('"')
        if not entry:
            continue
        path = Path(entry)
        try:
            if not path.is_dir():
                continue
        except OSError:
            continue
        if _IS_WINDOWS:
            # MXC accepts only drive-letter absolute paths; skip UNC and relative entries.
            if len(entry) < 3 or entry[1] != ":":
                continue
        seen.setdefault(str(path), None)
        if len(seen) >= 48:
            break
    return [_glob(Path(p)) for p in seen]


def _build_cache_dirs() -> list[Path]:
    """Build/package-manager caches the Workspace Exec preset lets commands write.

    A compiler or package manager that cannot write its own cache fails in ways that read
    like a broken toolchain, which is exactly what Workspace Exec exists to prevent. Only
    existing directories: a grant naming an absent path is a policy error."""
    local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    home = Path.home()
    candidates = (
        [local / "npm-cache", local / "pip" / "Cache", local / "uv" / "cache", local / "go-build",
         home / ".cargo" / "registry", home / ".m2" / "repository", home / ".gradle" / "caches",
         home / ".nuget" / "packages"]
        if _IS_WINDOWS else
        [home / ".npm", home / ".cache" / "pip", home / ".cache" / "uv", home / ".cache" / "go-build",
         home / ".cargo" / "registry", home / ".m2" / "repository", home / ".gradle" / "caches"]
    )
    return [path for path in candidates if path.is_dir()]


def _extra_globs(entries: tuple[str, ...]) -> list[str]:
    """Configured read/write paths (``~`` allowed), keeping only those that exist."""
    kept = []
    for entry in entries:
        path = Path(os.path.expanduser(entry))
        if path.is_dir():
            kept.append(_glob(path))
        else:
            logger.warning("[sandbox] ignoring profile path %r: it is not a directory", entry)
    return kept


_OVERLAY_SECTIONS = ("fs", "net", "proc", "syscall", "secrets")
_OVERLAY_LISTS = ("tools", "envFromHost")
_OVERLAY_MAPS = ("env",)


def _merge_policy_overlay(spec: dict[str, Any], overlay: dict[str, Any]) -> None:
    """Merge a profile's sandctl ``spec`` fragment over the generated policy.

    Lists union instead of replacing, so an override can add grants and denies but can
    never drop the baseline ones (cwd/scratch reads, the credential deny list). Unknown
    keys are ignored with a warning: keeping the generated value is the stricter side.
    """
    for section, value in overlay.items():
        if section in _OVERLAY_SECTIONS:
            if not isinstance(value, dict):
                logger.warning("[sandbox] profile policy.%s must be a mapping; ignored", section)
                continue
            target = spec.setdefault(section, {})
            if not isinstance(target, dict):
                logger.warning("[sandbox] profile policy.%s cannot merge into the generated %r; ignored",
                               section, type(target).__name__)
                continue
            for key, item in value.items():
                current = target.get(key)
                target[key] = (list(dict.fromkeys([*current, *item]))
                               if isinstance(current, list) and isinstance(item, list) else item)
        elif section in _OVERLAY_LISTS and isinstance(value, list):
            current = spec.get(section) or []
            spec[section] = list(dict.fromkeys([*(current if isinstance(current, list) else []), *value]))
        elif section in _OVERLAY_MAPS and isinstance(value, dict):
            merged = spec.get(section) if isinstance(spec.get(section), dict) else {}
            spec[section] = {**merged, **value}
        else:
            logger.warning("[sandbox] profile policy.%s is not a sandctl spec key; ignored", section)


def build_policy(
    *, cwd: str, scratch: str, shell: SandboxShell, profile: SandboxProfile | None = None,
) -> dict:
    """The boundary for one working directory under *profile* (default Workspace Write).

    Reads: cwd, scratch, the shell, PATH toolchains, the agent's interpreter, plus any
    configured read paths. Writes: cwd and scratch under Workspace Write; Workspace Exec
    adds the build caches. Read only grants none. Network stays denied in every preset."""
    profile = profile or SandboxProfile(WORKSPACE_WRITE, WORKSPACE_WRITE)
    read = [_glob(Path(cwd)), _glob(Path(scratch)), _glob(Path(shell.path).parent)]
    read += _toolchain_read_globs() + _interpreter_read_globs() + _extra_globs(profile.read)
    read = list(dict.fromkeys(read))
    writes: list[Path] = [] if profile.preset == READ_ONLY else [Path(cwd), Path(scratch)]
    write = [_glob(path) for path in writes]
    if profile.preset == WORKSPACE_EXEC:
        write += [_glob(path) for path in _build_cache_dirs()]
    if profile.preset != READ_ONLY:
        write += _extra_globs(profile.write)
    write = list(dict.fromkeys(write))
    spec: dict[str, Any] = {
        "fs": {
            "read": read[:64],
            "write": write,
            "deny": [
                g for g in map(_expand_deny, _DENY_GLOBS)
                if _deny_entry_usable(g, tuple(writes))
            ],
        },
        "net": {"default": "deny"},
    }
    if profile.policy:
        # Read only promises no writes and only the generated read set; a policy
        # fragment may add network/syscall/caps but never re-open the filesystem.
        overlay = (
            {section: value for section, value in profile.policy.items() if section != "fs"}
            if profile.preset == READ_ONLY else profile.policy
        )
        _merge_policy_overlay(spec, overlay)
    return {
        "apiVersion": "sandctl/v1",
        "kind": "Policy",
        "metadata": {"id": "hermes-local-gate", "threat": "T1"},
        "spec": spec,
    }


def policy_path(*, cwd: str, scratch: str, backend: str, profile: str = "") -> Path:
    """Stable per-(cwd, scratch, backend, profile) policy location outside the grants.

    The profile is part of the key: two profiles over one workspace must not overwrite each
    other's policy while both are in use."""
    from tools.environments.base import get_sandbox_dir

    digest = hashlib.sha256(
        "\0".join((os.path.realpath(cwd), os.path.realpath(scratch), backend, profile)).encode("utf-8", "surrogatepass")
    ).hexdigest()[:16]
    return get_sandbox_dir() / "command-gate" / digest / "policy.yaml"


def publish_policy(path: Path, policy: dict) -> Path:
    """Write *policy* atomically; the sandbox reads it, never writes it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".tmp.{os.getpid()}")
    tmp.write_text(yaml.safe_dump(policy, sort_keys=False, allow_unicode=True), encoding="utf-8")
    os.replace(tmp, path)
    return path


def _ps_literal(text: str) -> str:
    """Single-quoted PowerShell string literal (a quote doubles)."""
    return "'" + str(text).replace("'", "''") + "'"




def _encode_command(script: str) -> str:
    """UTF-16LE base64 for PowerShell's ``-EncodedCommand`` (no argv quoting at all)."""
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def _inner_status_lines() -> str:
    """Status capture that runs INSIDE the nested shell, directly after the user's text.

    Windows PowerShell 5.1 masks ``$?`` behind ``& {…}`` / ``Invoke-Expression`` (it stays
    True after a failed cmdlet), so the command is spliced as the last statement of the
    inner script — the only form where ``$?`` reports the cmdlet it followed. A command
    that calls ``exit`` never reaches these lines; its code is then the process exit code.
    """
    return (
        "\n$__hermes_ok = $?\n"
        "$__hermes_rc = if ($__hermes_ok) { 0 } elseif ($LASTEXITCODE -is [int] -and $LASTEXITCODE -ne 0) "
        "{ [int]$LASTEXITCODE } else { 1 }\n"
    )



def powershell_script(command: str, *, cwd: str, markers: ConfinedRun | None) -> str:
    """The confined PowerShell command: cd, run the model's text, emit markers.

    One process per command: there is no login profile, no snapshotted functions/aliases
    and no export carry-over, so each command starts from the process env (an explicit
    limit in the docs). ``markers=None`` is a background spawn: nothing finalizes its
    output, and the launcher's exit status reports the command's own.
    """
    lines = [
        "$ErrorActionPreference = 'Continue'\n",
        "$ProgressPreference = 'SilentlyContinue'\n",
        # 5.1 emits its own and native-child output in the console codepage (936 on zh-CN)
        # while the host decodes UTF-8: pin both to UTF-8 or non-ASCII text and paths turn
        # into mojibake (measured).
        "$OutputEncoding = [System.Text.UTF8Encoding]::new($false)\n",
        "[Console]::OutputEncoding = $OutputEncoding\n",
        "chcp 65001 > $null\n",
        f"Set-Location -LiteralPath {_ps_literal(cwd)}\n",
        command,
        _inner_status_lines(),
    ]
    if markers is not None:
        lines += [
            "Write-Output ''\n",
            f"Write-Output ({_ps_literal(markers.cwd_marker)} + (Get-Location).ProviderPath + {_ps_literal(markers.cwd_marker)})\n",
            "Write-Output ''\n",
            f"Write-Output ({_ps_literal(markers.rc_marker)} + $__hermes_rc + {_ps_literal(markers.rc_marker)})\n",
        ]
    lines.append("exit $__hermes_rc\n")
    return "".join(lines)


def shell_argv(shell: SandboxShell, script: str, *, login: bool = False) -> list[str]:
    """argv that runs *script* in *shell* (POSIX: ``bash -c``; PowerShell: -EncodedCommand)."""
    if shell.kind == "posix":
        return [shell.path, *(["-l"] if login else []), "-c", script]
    return [
        shell.path,
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        # Redirected stderr makes Windows PowerShell 5.1 emit CLIXML instead of text.
        "-OutputFormat", "Text",
        "-ExecutionPolicy", "Bypass",
        "-EncodedCommand", _encode_command(script),
    ]


def sandbox_argv(cfg: SandboxSettings, binary: str, run: ConfinedRun, script: str, *, login: bool = False) -> list[str]:
    """``sandctl run`` wrapping the shell invocation for one confined command."""
    argv = [binary, "run", "-p", str(run.policy_path), "--backend", run.backend, "--workdir", run.cwd]
    if cfg.allow_degraded:
        argv.append("--allow-degraded")
    return [*argv, "--", *shell_argv(run.shell, script, login=login)]


def _strip_marker(output: str, marker: str) -> tuple[str | None, str]:
    """Remove one ``marker<value>marker`` pair (last occurrence) and return its value."""
    last = output.rfind(marker)
    if last == -1:
        return None, output
    first = output.rfind(marker, max(0, last - 4096), last)
    if first == -1 or first == last:
        return None, output
    value = output[first + len(marker):last].strip()
    line_start = output.rfind("\n", 0, first)
    line_start = first if line_start == -1 else line_start
    line_end = output.find("\n", last + len(marker))
    line_end = len(output) if line_end == -1 else line_end + 1
    return value, output[:line_start] + output[line_end:]


_CLIXML_BLOCK_RE = re.compile(r"#< CLIXML\r?\n<Objs[^>]*>(.*?)</Objs>", re.DOTALL)
_CLIXML_TEXT_RE = re.compile(r'<S S="Error">(.*?)</S>', re.DOTALL)
_CLIXML_ESCAPE_RE = re.compile(r"_x([0-9A-Fa-f]{4})_")


def _decode_clixml(output: str) -> str:
    """A redirected PowerShell stderr arrives as CLIXML on this host; restore it to text.

    The payload is the same error record PowerShell prints to a console, so the model can
    read it; progress records are dropped (only ``S="Error"`` entries are decoded)."""
    if "#< CLIXML" not in output:
        return output

    def _block(match: re.Match[str]) -> str:
        parts = [
            html.unescape(_CLIXML_ESCAPE_RE.sub(lambda m: chr(int(m.group(1), 16)), chunk))
            for chunk in _CLIXML_TEXT_RE.findall(match.group(1))
        ]
        return "".join(parts)

    return _CLIXML_BLOCK_RE.sub(_block, output)


def _tail(output: str, lines: int = 4) -> str:
    kept = [ln for ln in output.strip().splitlines() if ln.strip()][-lines:]
    return "\n".join(f"  {ln}" for ln in kept)


# Stable sandctl text we consume (verified against the local build). Everything below is
# repeated boilerplate: it is logged once per process instead of riding every tool result.
_EXITED_RE = re.compile(r"sandctl: the command exited with code (\d+)")
_NOISE_PREFIXES = (
    "sandctl: limitation:",
    "sandctl: the command exited with code ",
    "the command inside the sandbox failed",
)
_READ_ALLOWLIST_WARN = "[WARN] The read allowlist is not enforced"
_reported_notes: set[str] = set()
_reported_lock = threading.Lock()


def _split_noise(output: str) -> tuple[str, list[str]]:
    """Drop sandctl's repeated advisory lines; keep stderr the command produced itself."""
    kept: list[str] = []
    dropped: list[str] = []
    skip_continuation = False
    for line in output.splitlines():
        if skip_continuation and line.startswith("       "):
            dropped.append(line)
            continue
        skip_continuation = False
        if line.startswith(_READ_ALLOWLIST_WARN):
            dropped.append(line)
            skip_continuation = True
            continue
        if line.startswith(_NOISE_PREFIXES):
            dropped.append(line)
            continue
        kept.append(line)
    return "\n".join(kept), dropped


def _note_once(dropped: list[str]) -> str:
    """Log sandctl's advisories once per process and return the one-time visible note."""
    if not dropped:
        return ""
    if not any(line.startswith(_READ_ALLOWLIST_WARN) for line in dropped):
        return ""
    note = "the read allowlist is advisory on this host"
    with _reported_lock:
        if note in _reported_notes:
            return ""
        _reported_notes.add(note)
    logger.warning("sandctl boundary is weaker than the policy asks on this host:\n%s", "\n".join(dropped))
    return ("[WARN] This host cannot confine reads to the grants: paths outside them stay "
            "readable. Writes, the deny list and network egress are enforced. Full detail in the Kouri Harness log.\n")


def failure_note(returncode: int, output: str) -> str:
    """The message a refused/blocked run needs: what happened and what to do about it."""
    if returncode == BLOCKED_EXIT:
        headline = "The command sandbox blocked this command at the boundary."
    elif returncode == REFUSED_EXIT:
        headline = "The command sandbox refused to run this command."
    else:
        return ""
    detail = _tail(output)
    lines = [f"[sandbox] {headline} The command did not run as written."]
    if detail:
        lines.append(f"[sandbox] Sandbox said:\n{detail}")
    lines.append(
        "[sandbox] Every command runs inside the sandctl boundary unless the session has Full Access. "
        "To run only THIS command at a wider level, pass sandbox=\"workspace-exec\" or "
        "sandbox=\"full-access\" on the terminal call; the user is asked to approve it, and the "
        "configured profile applies again to the next command. "
        f"To widen the session, change terminal.sandbox in {display_hermes_home()}/config.yaml.")
    return "\n".join(lines) + "\n"


def finalize(result: dict, run: ConfinedRun) -> dict:
    """Replace sandctl's exit status with the command's own and explain a refused run.

    Plain mode collapses any child failure to exit 1, so the status comes from the
    wrapper's marker; a command that called ``exit`` before the marker is still named on
    sandctl's own line, and only a run that never started (refused/blocked, exit 3/4)
    needs the fail-closed note."""
    output = _decode_clixml(result.get("output") or "")
    rc_text, output = _strip_marker(output, run.rc_marker)
    sandctl_rc = int(result.get("returncode") or 0)
    clean, dropped = _split_noise(output)
    if rc_text is not None:
        try:
            result["returncode"] = int(rc_text)
        except ValueError:
            logger.debug("Unreadable command exit marker %r", rc_text)
    else:
        match = _EXITED_RE.search(output)
        if match:
            result["returncode"] = int(match.group(1))
        else:
            note = failure_note(sandctl_rc, clean)
            if note:
                clean = note + clean
    result["output"] = _note_once(dropped) + clean
    return result


def marker_id() -> str:
    return uuid.uuid4().hex[:12]


def make_run(
    *, shell: SandboxShell, cfg: SandboxSettings, cwd: str, scratch: str, cwd_marker: str = "",
) -> ConfinedRun:
    """Build the per-command confinement record (policy published before the spawn)."""
    profile = active_profile(cfg)
    backend = profile.backend or cfg.backend
    path = policy_path(cwd=cwd, scratch=scratch, backend=backend, profile=profile.name)
    publish_policy(path, build_policy(cwd=cwd, scratch=scratch, shell=shell, profile=profile))
    return ConfinedRun(
        shell=shell,
        settings=cfg,
        policy_path=path,
        cwd=cwd,
        rc_marker=f"__HERMES_RC_{marker_id()}__",
        cwd_marker=cwd_marker,
        backend=backend,
    )


def background_command(*, command: str, cwd: str, scratch: str) -> str:
    """The ``sandctl run …`` command line a background session tracks.

    The tracked process is the launcher itself, so ending the session ends the sandbox
    and everything inside it. The command is shell-quoted because the registry spawns it
    through its login shell; no markers are emitted (nothing finalizes a background
    session, and the launcher's exit status is the command's own)."""
    cfg = settings()
    binary = resolve_binary(cfg)
    if binary is None:
        raise RuntimeError(missing_binary_message(cfg))
    shell = confined_shell()
    if shell is None:
        raise RuntimeError(unsupported_shell_message())
    run = make_run(shell=shell, cfg=cfg, cwd=cwd, scratch=scratch)
    script = command if shell.kind == "posix" else powershell_script(command, cwd=cwd, markers=None)
    return shlex.join(sandbox_argv(cfg, binary, run, script))


def missing_binary_message(cfg: SandboxSettings) -> str:
    return (
        "[sandbox] The command sandbox is enabled but its launcher is not installed, "
        "so commands cannot run. " + f"Please install `{cfg.binary}` on PATH"
        + (f" or set terminal.sandbox.binary in {display_hermes_home()}/config.yaml." if cfg.binary == "sandctl" else ".")
    )


def unsupported_shell_message() -> str:
    return (
        "[sandbox] This host has no shell the command sandbox can run: a confined command "
        "runs under Windows PowerShell, which is missing from PATH."
    )


__all__ = [
    "BLOCKED_EXIT", "FULL_ACCESS", "REFUSED_EXIT", "ConfinedRun", "SandboxProfile",
    "SandboxSettings", "SandboxShell", "active", "active_profile", "background_command",
    "build_policy", "confined_shell", "escalation", "failure_note", "file_tools_native",
    "file_writes_read_only", "finalize", "make_run", "missing_binary_message", "policy_path",
    "powershell_script", "publish_policy", "resolve_binary", "sandbox_argv", "settings",
    "shell_argv", "unsupported_shell_message", "widening",
]
