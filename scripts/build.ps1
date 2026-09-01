param(
    [string]$Version = "0.3.0",
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

python -m pytest

$pyinstaller = Get-Command pyinstaller -ErrorAction SilentlyContinue
if (-not $pyinstaller) {
    throw "PyInstaller is not installed. Install it with: python -m pip install pyinstaller"
}

pyinstaller --clean --noconfirm packaging/spd-model-injector.spec
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE."
}

if ($SkipInstaller) {
    Write-Host "Skipping Inno Setup installer."
    exit 0
}

$iscc = Get-Command iscc -ErrorAction SilentlyContinue
if (-not $iscc) {
    throw "Inno Setup compiler 'iscc' was not found. Install Inno Setup and add it to PATH, or rerun with -SkipInstaller."
}

iscc "/DMyAppVersion=$Version" packaging/spd-model-injector.iss
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup failed with exit code $LASTEXITCODE."
}
