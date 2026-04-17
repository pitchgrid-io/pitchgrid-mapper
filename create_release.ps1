# Create GitHub release script for PitchGrid Mapper Windows
# Usage: .\create_release.ps1 [version]

param(
    [string]$Version
)

$ErrorActionPreference = "Stop"

# Load environment variables from .env file
if (Test-Path ".env") {
    Get-Content ".env" | ForEach-Object {
        if ($_ -match "^\s*([^#][^=]+)=(.*)$") {
            $name = $matches[1].Trim()
            $value = $matches[2].Trim().Trim('"').Trim("'")
            [Environment]::SetEnvironmentVariable($name, $value, "Process")
        }
    }
}

$AppName = $env:APP_NAME
if (-not $AppName) { $AppName = "PitchGrid Mapper" }

# Version is always derived from pyproject.toml unless explicitly passed in
# as a parameter — overriding any value that may have leaked in from .env so
# the single source of truth is the project manifest, not a local dotfile.
if (-not $Version) {
    $Version = & (Join-Path $PSScriptRoot 'scripts\Get-AppVersion.ps1')
}
$env:APP_VERSION = $Version

Write-Host "Creating GitHub release for $AppName v$Version..." -ForegroundColor Cyan

# Find the installer
$OutputDir = "Installers\Windows\Output"
$InstallerFile = Get-ChildItem "$OutputDir\*.exe" -ErrorAction SilentlyContinue |
                 Sort-Object LastWriteTime -Descending |
                 Select-Object -First 1

if (-not $InstallerFile) {
    Write-Host "Error: Installer not found in $OutputDir" -ForegroundColor Red
    Write-Host "Run the following first:" -ForegroundColor Yellow
    Write-Host "  1. .\build_app.ps1" -ForegroundColor Yellow
    Write-Host "  2. .\sign_app.ps1" -ForegroundColor Yellow
    Write-Host "  3. .\create_installer.ps1" -ForegroundColor Yellow
    Write-Host "  4. .\sign_installer.ps1" -ForegroundColor Yellow
    exit 1
}

$InstallerPath = $InstallerFile.FullName
Write-Host "  Found installer: $InstallerPath"

# Verify installer is signed
Write-Host ""
Write-Host "Verifying installer signature..." -ForegroundColor Yellow
$sig = Get-AuthenticodeSignature $InstallerPath

if ($sig.Status -eq "Valid") {
    Write-Host "  Signature: Valid (Azure Trusted Signing)" -ForegroundColor Green
}
elseif ($sig.Status -eq "UnknownError") {
    Write-Host "  Signature: Self-signed (testing only)" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "WARNING: This installer is self-signed!" -ForegroundColor Yellow
    Write-Host "For production releases, sign with Azure Trusted Signing (.\sign_installer.ps1)" -ForegroundColor Yellow
    Write-Host ""
    $continue = Read-Host "Continue with self-signed installer? (y/N)"
    if ($continue -ne "y" -and $continue -ne "Y") {
        Write-Host "Release cancelled" -ForegroundColor Yellow
        exit 0
    }
}
else {
    Write-Host "  Warning: Installer is not signed or has invalid signature" -ForegroundColor Red
    Write-Host "  Signature status: $($sig.Status)" -ForegroundColor Red
    Write-Host ""
    $continue = Read-Host "Continue anyway? (y/N)"
    if ($continue -ne "y" -and $continue -ne "Y") {
        Write-Host "Release cancelled" -ForegroundColor Yellow
        exit 0
    }
}

# Check if gh CLI is installed
Write-Host ""
Write-Host "Checking GitHub CLI..." -ForegroundColor Yellow

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    Write-Host "Error: GitHub CLI (gh) not found" -ForegroundColor Red
    Write-Host "Install with: winget install GitHub.cli" -ForegroundColor Yellow
    Write-Host "Or download from: https://cli.github.com/" -ForegroundColor Yellow
    exit 1
}
Write-Host "  GitHub CLI found"

# Check if authenticated
Write-Host ""
Write-Host "Checking GitHub authentication..." -ForegroundColor Yellow

$authStatus = gh auth status 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "Error: Not authenticated with GitHub" -ForegroundColor Red
    Write-Host "Run: gh auth login" -ForegroundColor Yellow
    exit 1
}
Write-Host "  Authenticated"

# Load release notes from file
if (-not (Test-Path "RELEASE_NOTES.md")) {
    Write-Host "Error: RELEASE_NOTES.md not found" -ForegroundColor Red
    exit 1
}
$releaseNotes = Get-Content "RELEASE_NOTES.md" -Raw

# Check if release already exists
Write-Host ""
Write-Host "Checking if release v$Version exists..." -ForegroundColor Yellow

$releaseExists = gh release view "v$Version" 2>$null
$releaseExistsCode = $LASTEXITCODE

if ($releaseExistsCode -eq 0) {
    # Release exists, upload as additional asset
    Write-Host "  Release v$Version already exists" -ForegroundColor Yellow
    Write-Host "  Uploading Windows installer as additional asset..." -ForegroundColor Cyan

    try {
        gh release upload "v$Version" "$InstallerPath" --clobber

        Write-Host ""
        Write-Host "Installer uploaded successfully!" -ForegroundColor Green
        Write-Host "  Version: v$Version" -ForegroundColor Cyan
        Write-Host "  Uploaded: $($InstallerFile.Name)" -ForegroundColor Cyan

        # Update release notes to include both platforms
        Write-Host ""
        Write-Host "Updating release notes..." -ForegroundColor Yellow
        gh release edit "v$Version" --notes $releaseNotes

        Write-Host ""
        Write-Host "View release at:" -ForegroundColor Yellow
        Write-Host "  https://github.com/$(gh repo view --json nameWithOwner -q .nameWithOwner)/releases/tag/v$Version"
    }
    catch {
        Write-Host ""
        Write-Host "Error uploading installer: $_" -ForegroundColor Red
        exit 1
    }
}
else {
    # Release doesn't exist, create it
    Write-Host "  Release v$Version does not exist, creating new release..." -ForegroundColor Cyan

    try {
        gh release create "v$Version" `
            --title "PitchGrid Mapper v$Version" `
            --notes $releaseNotes `
            "$InstallerPath"

        Write-Host ""
        Write-Host "Release created successfully!" -ForegroundColor Green
        Write-Host "  Version: v$Version" -ForegroundColor Cyan
        Write-Host "  Uploaded: $($InstallerFile.Name)" -ForegroundColor Cyan

        Write-Host ""
        Write-Host "View release at:" -ForegroundColor Yellow
        Write-Host "  https://github.com/$(gh repo view --json nameWithOwner -q .nameWithOwner)/releases/tag/v$Version"
    }
    catch {
        Write-Host ""
        Write-Host "Error creating release: $_" -ForegroundColor Red
        Write-Host ""
        Write-Host "Troubleshooting:" -ForegroundColor Yellow
        Write-Host "  1. Ensure you have push access to the repository" -ForegroundColor Yellow
        Write-Host "  2. Try: gh auth refresh -s write:packages" -ForegroundColor Yellow
        exit 1
    }
}
