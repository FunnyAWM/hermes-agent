# Full sealed build: the runtime (interpreter + venv + agent source) ships
# inside the package; tools beyond the interpreter resolve from the user's
# PATH, exactly as a headless build's do. NSIS only - a portable build would
# re-extract the multi-hundred-MB payload on every launch.
#
# uv travels with the interpreter: stage_manager_runtime resolves both from the
# staged store only (never PATH, never a lazy install), so a python-only subset
# fails pm-runtime with "stage pinned tools before building the manager runtime".
# Shipping the lock-matched uv also keeps `hermes pm` off whatever uv the user
# happens to have on PATH.
param([switch]$RebuildPayload)

$ErrorActionPreference = 'Stop'
$env:HERMES_DESKTOP_VARIANT = "bundled";
$env:HERMES_BUNDLE_TOOLS = "python,uv";
$env:HERMES_DESKTOP_LOCAL_ROOT = (Resolve-Path "$PSScriptRoot\..").Path;
$env:HERMES_PYTHON = (Resolve-Path "$PSScriptRoot\..\.venv\Scripts\python.exe").Path;
$env:ELECTRON_MIRROR = "https://npmmirror.com/mirrors/electron/";
$env:HTTP_PROXY = "";
$env:HTTPS_PROXY = "";
# scripts/build/node-deps.mjs spawns pnpm.exe directly (no shell, so a .CMD shim
# will not do). Prefer the managed pin when PATH only has a shim.
$hasPnpmExe = $env:PATH -split ';' | Where-Object { $_ -and (Test-Path (Join-Path $_ 'pnpm.exe')) }
if (-not $hasPnpmExe) {
  $managed = Get-ChildItem "$env:LOCALAPPDATA\hermes\tools\pnpm-*\pnpm.exe" -ErrorAction SilentlyContinue |
    Select-Object -First 1
  if ($managed) { $env:PATH = "$($managed.DirectoryName);$env:PATH" }
}
if (-not ($env:PATH -split ';' | Where-Object { $_ -and (Test-Path (Join-Path $_ 'pnpm.exe')) })) {
  throw "pnpm.exe (v12) not found on PATH or in the managed tool store; run 'hermes pm install pnpm'"
}
Push-Location "$PSScriptRoot\..\apps\desktop";
$savedIndex = $env:UV_DEFAULT_INDEX
$savedInsecureHost = $env:UV_INSECURE_HOST
$savedNoProxy = $env:NO_PROXY
$buildLock = $null
try {
  # The receipt, install stamp and NSIS output belong to one build at a time.
  $lockDir = [IO.Directory]::CreateDirectory((Join-Path $env:HERMES_DESKTOP_LOCAL_ROOT '.build'))
  $buildLock = [IO.File]::Open((Join-Path $lockDir.FullName '.desktop-build.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
  $env:UV_DEFAULT_INDEX = "http://mirrors.aliyun.com/pypi/simple/"
  $env:UV_INSECURE_HOST = "http://mirrors.aliyun.com"
  $env:NO_PROXY = ($savedNoProxy, "mirrors.aliyun.com" | Where-Object { $_ }) -join ","
  $stageOptions = if ($RebuildPayload) { @('--rebuild-payload') } else { @() }
  node scripts/build-fork-inputs.mjs @stageOptions
  if ($LASTEXITCODE -ne 0) { throw "desktop preparation failed ($LASTEXITCODE)" }
  pnpm run builder:fork --win nsis --x64 --publish never "-c.directories.output=release-full";
  if ($LASTEXITCODE -ne 0) { throw "packaging failed ($LASTEXITCODE)" }
} finally {
  if ($buildLock) { $buildLock.Dispose() }
  $env:UV_DEFAULT_INDEX = $savedIndex
  $env:UV_INSECURE_HOST = $savedInsecureHost
  $env:NO_PROXY = $savedNoProxy
  Pop-Location
}
