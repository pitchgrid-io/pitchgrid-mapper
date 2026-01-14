# Create Windows installer using Inno Setup
# Usage: .\create_installer.ps1

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
if (-not $AppName) { $AppName = "PGIsomap" }

$AppVersion = $env:APP_VERSION
if (-not $AppVersion) { $AppVersion = "0.1.0" }

Write-Host "Creating $AppName installer..." -ForegroundColor Cyan

# Check if build exists
if (-not (Test-Path "dist\$AppName\$AppName.exe")) {
    Write-Host "Error: Application not built. Run .\build_app.ps1 first" -ForegroundColor Red
    exit 1
}

# Find Inno Setup
$InnoSetupPath = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $InnoSetupPath)) {
    # Try alternate location
    $InnoSetupPath = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
}
if (-not (Test-Path $InnoSetupPath)) {
    Write-Host "Error: Inno Setup 6 not found" -ForegroundColor Red
    Write-Host "Install from: https://jrsoftware.org/isdl.php" -ForegroundColor Yellow
    Write-Host "Or use: winget install JRSoftware.InnoSetup" -ForegroundColor Yellow
    exit 1
}

Write-Host "  Found Inno Setup: $InnoSetupPath"

# Create output directory
$OutputDir = "Installers\Windows\Output"
if (-not (Test-Path $OutputDir)) {
    New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
}

# Run Inno Setup
Write-Host ""
Write-Host "Running Inno Setup compiler..." -ForegroundColor Yellow

$IssFile = "Installers\Windows\PGIsomap.iss"
$SourcePath = (Get-Location).Path

& $InnoSetupPath $IssFile `
    /DBUILDS_PATH="$SourcePath" `
    /DVERSION_STRING="$AppVersion" `
    /DVERSION_NUMBERS="$AppVersion" `
    /O"$OutputDir"

if ($LASTEXITCODE -ne 0) {
    Write-Host "Error: Inno Setup compilation failed" -ForegroundColor Red
    exit 1
}

# Find the output file
$InstallerFile = Get-ChildItem "$OutputDir\*.exe" | Sort-Object LastWriteTime -Descending | Select-Object -First 1

if ($InstallerFile) {
    Write-Host ""
    Write-Host "Installer created successfully!" -ForegroundColor Green
    Write-Host "  Output: $($InstallerFile.FullName)" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Next steps:" -ForegroundColor Yellow
    Write-Host "  1. Test the installer locally"
    Write-Host "  2. Sign with Azure Trusted Signing: .\sign_app.ps1"
}
else {
    Write-Host "Error: Installer file not found after build" -ForegroundColor Red
    exit 1
}
