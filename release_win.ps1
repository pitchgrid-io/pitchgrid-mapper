# Full Windows release script - builds and creates installer
# Usage: .\release_win.ps1

$ErrorActionPreference = "Stop"

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  PitchGrid Mapper - Windows Full Release" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""

# Load environment variables from .env if exists
if (Test-Path .env) {
    Get-Content .env | ForEach-Object {
        if ($_ -match "^([^#=]+)=(.*)$") {
            $name = $matches[1].Trim()
            $value = $matches[2].Trim().Trim("'`"")
            [Environment]::SetEnvironmentVariable($name, $value, "Process")
        }
    }
}

$AppName = if ($env:APP_NAME) { $env:APP_NAME } else { "PitchGrid Mapper" }

# Version is always derived from pyproject.toml — overriding any value that
# may have leaked in from .env so the single source of truth is the project
# manifest, not a local dotfile.
$AppVersion = & (Join-Path $PSScriptRoot 'scripts\Get-AppVersion.ps1')
$env:APP_VERSION = $AppVersion

Write-Host "App: $AppName"
Write-Host "Version: $AppVersion"
Write-Host ""

# Generate icons
Write-Host "==========================================" -ForegroundColor Yellow
Write-Host "  Generating Windows icons" -ForegroundColor Yellow
Write-Host "==========================================" -ForegroundColor Yellow
if (Test-Path generate_icons_win.ps1) {
    .\generate_icons_win.ps1
} else {
    Write-Host "  Icon generation script not found, skipping..." -ForegroundColor Gray
}

# Build frontend
Write-Host ""
Write-Host "==========================================" -ForegroundColor Yellow
Write-Host "  Building frontend" -ForegroundColor Yellow
Write-Host "==========================================" -ForegroundColor Yellow
Push-Location frontend
npm install
npm run build
Pop-Location

# Sync Python dependencies
Write-Host ""
Write-Host "==========================================" -ForegroundColor Yellow
Write-Host "  Syncing Python dependencies" -ForegroundColor Yellow
Write-Host "==========================================" -ForegroundColor Yellow
uv sync --extra build

# Clean previous builds
Write-Host ""
Write-Host "==========================================" -ForegroundColor Yellow
Write-Host "  Cleaning previous builds" -ForegroundColor Yellow
Write-Host "==========================================" -ForegroundColor Yellow
if (Test-Path build) { Remove-Item -Recurse -Force build }
if (Test-Path dist) { Remove-Item -Recurse -Force dist }

# Create version file
$AppVersion | Out-File -FilePath _version.txt -Encoding utf8 -NoNewline

# Build with PyInstaller
Write-Host ""
Write-Host "==========================================" -ForegroundColor Yellow
Write-Host "  Building with PyInstaller" -ForegroundColor Yellow
Write-Host "==========================================" -ForegroundColor Yellow
uv run pyinstaller pg_isomap_win.spec

# Clean up version file
Remove-Item -Force _version.txt -ErrorAction SilentlyContinue

# Create installer with Inno Setup (if available)
Write-Host ""
Write-Host "==========================================" -ForegroundColor Yellow
Write-Host "  Creating installer" -ForegroundColor Yellow
Write-Host "==========================================" -ForegroundColor Yellow

$InnoSetup = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
$InnoScript = "installer.iss"

if ((Test-Path $InnoSetup) -and (Test-Path $InnoScript)) {
    & $InnoSetup $InnoScript
    Write-Host "  Installer created successfully!" -ForegroundColor Green
} else {
    Write-Host "  Inno Setup not found or installer.iss missing" -ForegroundColor Gray
    Write-Host "  Skipping installer creation..." -ForegroundColor Gray
    Write-Host "  Build output is in: dist\$AppName\" -ForegroundColor Gray
}

Write-Host ""
Write-Host "==========================================" -ForegroundColor Green
Write-Host "  Build Complete!" -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Output location: dist\$AppName\"
Write-Host ""
Write-Host "To upload to GitHub release, run:" -ForegroundColor Cyan
Write-Host "  gh release upload v$AppVersion <installer.exe> --clobber"
