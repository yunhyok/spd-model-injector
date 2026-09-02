param(
    [string]$Version = "0.4.0",
    [string]$Repo = "spd-model-injector"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$gh = Get-Command gh -ErrorAction SilentlyContinue
if (-not $gh) {
    throw "GitHub CLI 'gh' was not found. Install it, run 'gh auth login', then rerun this script."
}

gh auth status

if (-not (git remote get-url origin 2>$null)) {
    gh repo create $Repo --private --source . --remote origin
}

git push -u origin (git branch --show-current)

$installer = "dist/installer/SPD-Model-Injector-Setup-$Version.exe"
if (-not (Test-Path $installer)) {
    throw "Installer artifact not found: $installer. Run scripts/build.ps1 first."
}

gh release create "v$Version" $installer --verify-tag --title "SPD Model Injector v$Version" --notes "Generate Port now supports DUT/LGA components by merging every Package.Node pin on the selected target and reference NETs into one PowerSI Port terminal. The Port workspace shows both pin counts before queueing."
