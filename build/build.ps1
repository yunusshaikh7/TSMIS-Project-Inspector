# Portable build for TSMIS Branch Identifier.
#
# Produces a self-contained onefolder under dist\TSMIS Branch Identifier\ that
# bundles Python and every dependency -- no installer and no Python required on
# the target machine. Zip that folder to distribute.
#
# Usage (from the repo root):
#   powershell -ExecutionPolicy Bypass -File build\build.ps1 [-SelfTest] [-RecreateVenv]
#
# -SelfTest runs the EXACT shipped windowed exe's `--self-test` over the pruned
# bundle afterwards, so the artifact that ships is the artifact that passed.

param(
    [switch]$SelfTest,
    [switch]$RecreateVenv
)

$ErrorActionPreference = "Stop"

$BuildDir = $PSScriptRoot
$RepoRoot = Split-Path -Parent $BuildDir
$VenvDir  = Join-Path $BuildDir ".venv"
$VenvPy   = Join-Path $VenvDir  "Scripts\python.exe"
$WorkDir  = Join-Path $BuildDir "pyi-work"
$DistDir  = Join-Path $RepoRoot "dist"

function Assert-LastExit($what) {
    if ($LASTEXITCODE -ne 0) { throw "$what failed (exit $LASTEXITCODE)" }
}

# --- 1. Isolated build venv with the pinned dependencies -------------------
if ($RecreateVenv -and (Test-Path $VenvDir)) {
    Write-Host "==> Removing build venv for a clean recreate"
    Remove-Item -Recurse -Force $VenvDir
}
if (-not (Test-Path $VenvPy)) {
    Write-Host "==> Creating build venv"
    $pyVer = & python --version
    if ("$pyVer" -notmatch "^Python 3\.11\.") {
        throw "Build requires CPython 3.11 on PATH (found '$pyVer')."
    }
    python -m venv $VenvDir; Assert-LastExit "venv creation"
}
Write-Host "==> Installing the pinned build dependencies"
& $VenvPy -m pip install --upgrade pip --quiet; Assert-LastExit "pip upgrade"
& $VenvPy -m pip install --quiet -r (Join-Path $RepoRoot "requirements-build.txt"); Assert-LastExit "pip install"

# --- 2. Package the windowed app as a portable onefolder -------------------
$env:TSMIS_ENTRY    = Join-Path $RepoRoot "scripts\gui_main.py"
$env:TSMIS_APP_NAME = "TSMIS Branch Identifier"
$env:TSMIS_CONSOLE  = "0"
Write-Host "==> Running PyInstaller"
& $VenvPy -m PyInstaller (Join-Path $BuildDir "app.spec") --distpath $DistDir --workpath $WorkDir --noconfirm
Assert-LastExit "PyInstaller"
$AppDir = Join-Path $DistDir $env:TSMIS_APP_NAME

# --- 3. User-facing doc BEFORE the prune + scan ----------------------------
Write-Host "==> Adding Start Here.txt"
Copy-Item (Join-Path $BuildDir "dist_readme.txt") (Join-Path $AppDir "Start Here.txt") -Force

# --- 4. Trim to runtime-only files + DLP guard ------------------------------
Write-Host "==> Pruning the bundle and scanning for DLP-blocked content"
& (Join-Path $BuildDir "prune_bundle.ps1") -Target $AppDir

# --- 5. Frozen exact-artifact self-test (the release gate) -----------------
if ($SelfTest) {
    $ExactExe = Join-Path $AppDir ("{0}.exe" -f $env:TSMIS_APP_NAME)
    $SelfTestOut = Join-Path $WorkDir "selftest-output.txt"
    if (Test-Path $SelfTestOut) { Remove-Item $SelfTestOut -Force }
    $env:TSMIS_SELFTEST_OUT = $SelfTestOut
    Write-Host "==> Running frozen self-test: `"$ExactExe`" --self-test"
    $proc = Start-Process -FilePath $ExactExe -ArgumentList "--self-test" -Wait -PassThru
    Remove-Item Env:TSMIS_SELFTEST_OUT
    if (Test-Path $SelfTestOut) {
        Get-Content $SelfTestOut | ForEach-Object { Write-Host "    $_" }
    }
    if ($proc.ExitCode -ne 0) {
        throw "frozen self-test failed (exit $($proc.ExitCode)) -- see output above"
    }
    Write-Host "==> Frozen self-test PASSED."
}

$SizeMB = (Get-ChildItem $AppDir -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
Write-Host ("`n==> Built {0}  ({1:N0} MB onefolder)" -f $AppDir, $SizeMB)
Write-Host "    Zip this folder to distribute (right-click -> Send to -> Compressed folder)."
