# T-12: NSIS per-user overwrite install must keep HERMES_HOME data intact.
#
# Never uninstalls anything. Preflight records whether a Hermes install already
# exists, seeds an isolated HERMES_HOME, runs the installer twice silently, launches
# the installed app briefly, and diffs the data snapshots taken before install #1
# and after install #2. Missing or changed files fail; added files are reported.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts/verify_nsis_overwrite.ps1 `
#     -Installer apps/desktop/release/Hermes-0.22.5-fork.1-win-x64-setup.exe
#
# Exit 0 only when both installs succeeded, the exe reports the expected version,
# and memories/ notes/ sessions/ are byte-identical. The work directory is kept on
# failure for inspection.

param(
    [Parameter(Mandatory = $true)][string]$Installer,
    [string]$WorkDir = (Join-Path $env:TEMP ("hermes-t12-" + [guid]::NewGuid().ToString("N"))),
    [int]$LaunchSeconds = 8
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = if ($env:HERMES_PYTHON) { $env:HERMES_PYTHON } else { Join-Path $repo ".venv\Scripts\python.exe" }
$installerPath = (Resolve-Path $Installer).Path
$installDir = Join-Path $env:LOCALAPPDATA "Programs\Hermes"
$exe = Join-Path $installDir "Hermes.exe"
$homeDir = Join-Path $WorkDir "hermes-home"
$realHome = Join-Path $env:USERPROFILE ".hermes"
$beforeHome = Join-Path $WorkDir "before-home.json"
$afterHome = Join-Path $WorkDir "after-home.json"
$beforeReal = Join-Path $WorkDir "before-real.json"
$afterReal = Join-Path $WorkDir "after-real.json"
$reportPath = Join-Path $WorkDir "report.json"
$report = [ordered]@{
    ok = $false
    installer = $installerPath
    installer_version = (Get-Item $installerPath).VersionInfo.ProductVersion
    install_dir = $installDir
    preexisting_install = $false
    preexisting_version = $null
    installed_version_first = $null
    installed_version_second = $null
    version_normalized = $null
    version_match = $false
    app_launch = $null
    home_diff = $null
    real_home_diff = $null
    workdir = $WorkDir
    error = $null
}

New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null
function Save-Report {
    $report | ConvertTo-Json -Depth 8 | Set-Content -Path $reportPath -Encoding utf8
}

# Windows version resources are four numeric fields; the fork suffix
# (`-fork.1`) cannot round-trip into Hermes.exe's ProductVersion, which
# electron-builder normalizes to `0.22.5.0`. Compare the numeric core of the
# installer's display version against the exe's file version instead.
function Get-WinVersionCore([string]$Value) {
    $m = [regex]::Match($Value, '^\d+(\.\d+){0,3}')
    if ($m.Success) { return $m.Value } else { return $Value }
}

# The exe pads the version resource to four fields (`0.22.5.0`); the installer's
# display version does not (`0.22.5-fork.1`). Compare every numeric component the
# installer declares.
function Test-VersionCompatible([string]$Installed, [string]$Expected) {
    $installedParts = @((Get-WinVersionCore $Installed).Split('.') | ForEach-Object { [int]$_ })
    $expectedParts = @((Get-WinVersionCore $Expected).Split('.') | ForEach-Object { [int]$_ })
    if ($expectedParts.Count -eq 0 -or $expectedParts.Count -gt $installedParts.Count) {
        return $false
    }
    for ($n = 0; $n -lt $expectedParts.Count; $n++) {
        if ($installedParts[$n] -ne $expectedParts[$n]) { return $false }
    }
    return $true
}

# A one-click NSIS install runs the app when it finishes (electron-builder's
# default runAfterFinish). Kill only processes whose image lives in the install
# tree so the second install can replace files and a pre-existing unrelated
# Hermes session is never touched.
function Stop-InstalledApp {
    $stale = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($installDir, "OrdinalIgnoreCase") }
    foreach ($p in $stale) {
        & taskkill.exe /PID $p.ProcessId /T /F 2>$null | Out-Null
    }
    Start-Sleep -Seconds 2
}

try {
    if (Test-Path $exe) {
        $report.preexisting_install = $true
        $report.preexisting_version = (Get-Item $exe).VersionInfo.ProductVersion
    }

    New-Item -ItemType Directory -Force -Path (Join-Path $homeDir "memories"),
        (Join-Path $homeDir "notes\work"), (Join-Path $homeDir "sessions\t12") | Out-Null
    Set-Content -Path (Join-Path $homeDir "memories\MEMORY.md") -Value "T12_MARKER memory" -Encoding utf8
    Set-Content -Path (Join-Path $homeDir "memories\USER.md") -Value "T12_MARKER user" -Encoding utf8
    Set-Content -Path (Join-Path $homeDir "notes\work\t12.md") `
        -Value "---`nname: t12`ndescription: overwrite preservation`n---`nT12_MARKER note" -Encoding utf8
    Set-Content -Path (Join-Path $homeDir "sessions\t12\meta.json") -Value '{"id":"t12"}' -Encoding utf8

    # Also covers the installers' runAfterFinish launch: it must never touch the
    # developer's real ~/.hermes.
    $env:HERMES_HOME = $homeDir

    & $python (Join-Path $repo "scripts\install_snapshot.py") --root $homeDir --json $beforeHome | Out-Null
    if (Test-Path $realHome) {
        & $python (Join-Path $repo "scripts\install_snapshot.py") --root $realHome --json $beforeReal | Out-Null
    }

    Start-Process -FilePath $installerPath -ArgumentList "/S" -Wait
    Start-Sleep -Seconds 3
    Stop-InstalledApp
    if (-not (Test-Path $exe)) { throw "install #1 did not produce $exe" }
    $report.installed_version_first = (Get-Item $exe).VersionInfo.ProductVersion

    $app = Start-Process -FilePath $exe -PassThru
    Start-Sleep -Seconds $LaunchSeconds
    $alive = -not $app.HasExited
    if ($alive) { & taskkill.exe /PID $app.Id /T /F | Out-Null }
    $report.app_launch = @{ started = $true; still_running_after_launch = $alive }

    Start-Sleep -Seconds 2
    Stop-InstalledApp
    Start-Process -FilePath $installerPath -ArgumentList "/S" -Wait
    Start-Sleep -Seconds 3
    Stop-InstalledApp
    if (-not (Test-Path $exe)) { throw "install #2 did not produce $exe" }
    $report.installed_version_second = (Get-Item $exe).VersionInfo.ProductVersion

    & $python (Join-Path $repo "scripts\install_snapshot.py") --root $homeDir --json $afterHome | Out-Null
    $homeReport = & $python (Join-Path $repo "scripts\install_snapshot.py") --compare $beforeHome $afterHome
    $homeExit = $LASTEXITCODE
    $report.home_diff = ($homeReport | ConvertFrom-Json)
    if (Test-Path $beforeReal) {
        & $python (Join-Path $repo "scripts\install_snapshot.py") --root $realHome --json $afterReal | Out-Null
        $realReport = & $python (Join-Path $repo "scripts\install_snapshot.py") --compare $beforeReal $afterReal
        $realExit = $LASTEXITCODE
        $report.real_home_diff = ($realReport | ConvertFrom-Json)
    } else {
        $realExit = 0
    }

    $report.version_normalized = Get-WinVersionCore $report.installer_version
    $report.version_match =
        (Test-VersionCompatible $report.installed_version_first $report.installer_version) -and
        (Test-VersionCompatible $report.installed_version_second $report.installer_version)

    $report.ok = ($homeExit -eq 0) -and ($realExit -eq 0) -and $report.version_match
    Save-Report
    $report | ConvertTo-Json -Depth 8
    if (-not $report.ok) { exit 1 }
    exit 0
} catch {
    $report.error = $_.Exception.Message
    Save-Report
    $report | ConvertTo-Json -Depth 8
    exit 2
}
