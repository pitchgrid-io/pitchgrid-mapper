# Build script for PG Isomap Windows application
# Usage: .\build_app.ps1

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

Write-Host "Building $AppName $AppVersion Windows application..." -ForegroundColor Cyan

# Check for required tools
Write-Host "Checking prerequisites..." -ForegroundColor Yellow

# Check for Python
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "Error: Python is not installed or not in PATH" -ForegroundColor Red
    Write-Host "Install Python 3.12 from https://www.python.org/downloads/" -ForegroundColor Yellow
    exit 1
}

$pythonVersion = python --version 2>&1
Write-Host "  Found: $pythonVersion"

# Check for uv
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "Error: uv is not installed" -ForegroundColor Red
    Write-Host "Install with: irm https://astral.sh/uv/install.ps1 | iex" -ForegroundColor Yellow
    exit 1
}
Write-Host "  Found: uv"

# Check for Node.js
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Write-Host "Error: Node.js/npm is not installed" -ForegroundColor Red
    Write-Host "Install from https://nodejs.org/" -ForegroundColor Yellow
    exit 1
}
Write-Host "  Found: npm"

# Generate Windows icon from PNG (requires ImageMagick)
Write-Host ""
Write-Host "Generating Windows icon..." -ForegroundColor Yellow

if (Test-Path "icons/icon_256x256.png") {
    # Use ImageMagick if available
    $magickPath = "C:\Program Files\ImageMagick-7.1.2-Q16-HDRI\magick.exe"
    if (Test-Path $magickPath) {
        & $magickPath convert "icons/icon_256x256.png" -define icon:auto-resize=256,128,64,48,32,16 "$AppName.ico"
        Write-Host "  Created $AppName.ico using ImageMagick"
    }
    elseif (Test-Path "$AppName.ico") {
        Write-Host "  Using existing $AppName.ico"
    }
    else {
        Write-Host "  Warning: ImageMagick not found, skipping icon generation" -ForegroundColor Yellow
        Write-Host "  Install with: winget install ImageMagick.ImageMagick" -ForegroundColor Yellow
    }
}
else {
    Write-Host "  Warning: icons/icon_256x256.png not found" -ForegroundColor Yellow
}

# Build frontend
Write-Host ""
Write-Host "Building frontend..." -ForegroundColor Yellow
Push-Location frontend
npm install
npm run build
Pop-Location
Write-Host "  Frontend built successfully"

# Sync Python dependencies
Write-Host ""
Write-Host "Syncing Python dependencies..." -ForegroundColor Yellow
uv sync --extra build
Write-Host "  Dependencies synced"

# Clean previous builds
Write-Host ""
Write-Host "Cleaning previous builds..." -ForegroundColor Yellow
if (Test-Path "build") { Remove-Item -Recurse -Force "build" }
if (Test-Path "dist") { Remove-Item -Recurse -Force "dist" }
Write-Host "  Cleaned"

# Build with PyInstaller
Write-Host ""
Write-Host "Building application with PyInstaller..." -ForegroundColor Yellow
uv run pyinstaller pg_isomap_win.spec
Write-Host "  PyInstaller build complete"

# Verify build output
if (-not (Test-Path "dist\$AppName\$AppName.exe")) {
    Write-Host "Error: Build failed - executable not found" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "Build completed successfully!" -ForegroundColor Green
Write-Host "  Output: dist\$AppName\" -ForegroundColor Cyan
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "  1. Test the application: .\dist\$AppName\$AppName.exe"
Write-Host "  2. Create installer: .\create_installer.ps1"
Write-Host "  3. Sign installer: .\sign_app.ps1"
