param(
    [string]$Version = "0.8.0",
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
if ($LASTEXITCODE -ne 0) {
    throw "GitHub authentication failed. Release stopped."
}

$installer = "dist/installer/SPD-Model-Injector-Setup-$Version.exe"
if (-not (Test-Path $installer)) {
    throw "Installer artifact not found: $installer. Run scripts/build.ps1 first."
}

if (-not (git remote get-url origin 2>$null)) {
    gh repo create $Repo --private --source . --remote origin
    if ($LASTEXITCODE -ne 0) {
        throw "GitHub repository creation failed. Release stopped."
    }
}

git push -u origin (git branch --show-current)
if ($LASTEXITCODE -ne 0) {
    throw "Git push failed. Release stopped."
}

gh release create "v$Version" $installer --verify-tag --title "SPD Model Injector v$Version" --generate-notes
if ($LASTEXITCODE -ne 0) {
    throw "GitHub release creation failed."
}
