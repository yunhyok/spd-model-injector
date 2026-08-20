param(
    [string]$Version = "0.1.7",
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

gh release create "v$Version" $installer --title "SPD Model Injector v$Version" --notes "Adds alternate-header and partial RefDes activation-status Excel imports, plus Net Name data in RefDes Excel exports."
