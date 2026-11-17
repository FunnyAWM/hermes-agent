# Full sealed build: the runtime (interpreter + venv + agent source) ships
# inside the package; tools beyond the interpreter resolve from the user's
# PATH, exactly as a headless build's do. NSIS only - a portable build would
# re-extract the multi-hundred-MB payload on every launch.
$env:HERMES_DESKTOP_VARIANT = "bundled";
$env:HERMES_BUNDLE_TOOLS = "python";
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
try {
  pnpm run payload; if ($LASTEXITCODE -ne 0) { throw "payload staging failed ($LASTEXITCODE)" }
  pnpm run build; if ($LASTEXITCODE -ne 0) { throw "desktop build failed ($LASTEXITCODE)" }
  pnpm exec electron-builder --config electron-builder.fork.cjs --win nsis --x64 --publish never "-c.directories.output=release-full";
  if ($LASTEXITCODE -ne 0) { throw "packaging failed ($LASTEXITCODE)" }
} finally { Pop-Location }
