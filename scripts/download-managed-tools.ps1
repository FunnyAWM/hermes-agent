#Requires -Version 5.1
<#
.SYNOPSIS
    Download every managed tool package the Windows x64 desktop payload needs.

.DESCRIPTION
    Resumes into a build-owned store; re-running verifies existing entries and
    skips them. The default store is outside user state, so this script does not
    touch %LOCALAPPDATA%\hermes application data.

    Package selection comes from pm/lock.json for the current target. Packages
    that declare the target unsupported (for example iron-proxy on Windows) are
    skipped automatically.

.PARAMETER Store
    Destination tool store. Default: <repo>\.cache\desktop-inputs\tools

.PARAMETER StageToPayload
    After a complete download, copy the verified tools into
    apps\desktop\build\agent-payload\tools so `npm run payload` can reuse them
    without downloading the tool layer again.

.PARAMETER ListOnly
    Print the resolved package set and exit without downloading.

.PARAMETER Retries
    Number of full passes after the first one when network failures occur.
    Default: 3.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\download-managed-tools.ps1 -ListOnly

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\download-managed-tools.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\download-managed-tools.ps1 -StageToPayload
#>
[CmdletBinding()]
param(
    [string]$Store = "",
    [string]$Python = "",
    [switch]$StageToPayload,
    [switch]$ListOnly,
    [int]$Retries = 3
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $PSScriptRoot
if (-not $Store) {
    $Store = Join-Path $repo ".cache\desktop-inputs\tools"
}
$Store = [System.IO.Path]::GetFullPath($Store)
$payloadStore = [System.IO.Path]::GetFullPath((Join-Path $repo "apps\desktop\build\agent-payload\tools"))
$logDir = Split-Path -Parent $Store
$log = Join-Path $logDir "download-managed.log"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

if (-not $Python) {
    $candidates = @(
        (Join-Path $repo ".venv\Scripts\python.exe"),
        (Join-Path $repo "venv\Scripts\python.exe")
    )
    $Python = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if (-not $Python -or -not (Test-Path -LiteralPath $Python)) {
    throw "No Python interpreter found. Create .venv first (uv sync --all-extras --no-extra kittentts) or pass -Python <path>."
}
$Python = (Resolve-Path -LiteralPath $Python).Path

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$worker = @'
import sys
from pathlib import Path

from pm import prepare_tools, stage_tools
from pm.install import _lockfile
from pm.lock import Facts
from pm.registry import get_package
from pm.store import Store, current_target

mode = sys.argv[1]
store = Path(sys.argv[2]).resolve()
payload = Path(sys.argv[3]).resolve()
stage = sys.argv[4] == "1"
target = current_target()
lock = _lockfile()

names = [name for name in lock.names() if not get_package(name).internal or name == "uv"]
if "python" not in names:
    names.append("python")
names = [name for name in names if get_package(name).missing_reason(target) is None]

print(f"target={target} packages={len(names)} store={store}", flush=True)
for name in names:
    print(f"  - {name} {lock.version(name)}", flush=True)
if mode == "list":
    raise SystemExit(0)

store.mkdir(parents=True, exist_ok=True)
store_handle = Store(store)
failures = []
for index, name in enumerate(names, 1):
    print(f"[{index}/{len(names)}] {name} {lock.version(name)}", flush=True)
    try:
        prepare_tools([name], out=store, target=target)
    except Exception as exc:
        failures.append((name, str(exc)))
        print(f"  FAIL {name}: {exc}", flush=True)
        continue
    fact = Facts(store / "facts.json").get(name)
    entry = fact.get("entry") if fact else None
    if not entry:
        print(f"  WARN {name}: facts entry is missing", flush=True)
        failures.append((name, "facts entry is missing"))
        continue
    root = store_handle.entry(entry)
    size = sum(path.stat().st_size for path in root.rglob("*") if path.is_file())
    print(f"  OK {entry} ({size / (1024 * 1024):.1f} MiB)", flush=True)

if failures:
    print(f"FAILED {len(failures)} package(s):", flush=True)
    for name, reason in failures:
        print(f"  - {name}: {reason}", flush=True)
    raise SystemExit(1)

print(f"DONE {len(names)} package(s) verified in {store}", flush=True)
if stage:
    print(f"staging verified tools into {payload}", flush=True)
    stage_tools(names, source_store=store, out=payload, target=target)
    print(f"STAGED {payload}", flush=True)
else:
    print("Next: re-run with -StageToPayload to populate apps\\desktop\\build\\agent-payload\\tools.", flush=True)
'@

Write-Host "Repository : $repo"
Write-Host "Interpreter: $Python"
Write-Host "Store      : $Store"
Write-Host "Log        : $log"
Write-Host ""

Push-Location -LiteralPath $repo
try {
    $mode = if ($ListOnly) { "list" } else { "download" }
    $stageFlag = if ($StageToPayload -and -not $ListOnly) { "1" } else { "0" }
    $attempts = [Math]::Max(1, $Retries)
    $exit = 1
    for ($attempt = 1; $attempt -le $attempts; $attempt++) {
        if (-not $ListOnly) {
            Write-Host "=== pass $attempt / $attempts ==="
        }
        $previous = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            $worker | & $Python -u - $mode $Store $payloadStore $stageFlag 2>&1 |
                Tee-Object -FilePath $log -Append
            $exit = $LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $previous
        }
        if ($exit -eq 0) { break }
        if ($attempt -lt $attempts) {
            $delay = [Math]::Min(30, 5 * $attempt)
            Write-Host "Retrying in $delay seconds (exit $exit)..."
            Start-Sleep -Seconds $delay
        }
    }
    if ($exit -ne 0) {
        throw "Managed tool download failed after $attempts pass(es). See $log"
    }
}
finally {
    Pop-Location
}

if (-not $ListOnly) {
    Write-Host ""
    Write-Host "Managed tools are ready."
    if (-not $StageToPayload) {
        Write-Host "For an offline `npm run payload`, re-run with -StageToPayload."
    }
    Write-Host "Python wheels still come from PyPI during the payload venv step unless the uv cache is warm."
}
