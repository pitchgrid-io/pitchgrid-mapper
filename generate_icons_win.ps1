# Generate Windows .ico file from PNG icons
# Requires ImageMagick: winget install ImageMagick.ImageMagick
#
# Usage: .\generate_icons_win.ps1

$ErrorActionPreference = "Stop"

$AppName = $env:APP_NAME
if (-not $AppName) { $AppName = "PGIsomap" }

Write-Host "Generating Windows icon for $AppName..." -ForegroundColor Cyan

# Check for ImageMagick
if (-not (Get-Command magick -ErrorAction SilentlyContinue)) {
    Write-Host "Error: ImageMagick not found" -ForegroundColor Red
    Write-Host "Install with: winget install ImageMagick.ImageMagick" -ForegroundColor Yellow
    exit 1
}

# Check for source images
$IconsDir = "icons"
$SourcePng = "$IconsDir\icon_256.png"

if (-not (Test-Path $SourcePng)) {
    # Try to generate from SVG if available
    $SvgSource = "$IconsDir\icon_app.svg"
    if (-not $SvgSource) {
        $SvgSource = "assets\icon_app.svg"
    }

    if (Test-Path $SvgSource) {
        Write-Host "  Generating PNG from SVG..."

        # Create icons directory
        if (-not (Test-Path $IconsDir)) {
            New-Item -ItemType Directory -Path $IconsDir | Out-Null
        }

        # Generate PNG sizes using ImageMagick
        $sizes = @(16, 32, 48, 64, 128, 256, 512, 1024)
        foreach ($size in $sizes) {
            magick convert -background none -density 300 $SvgSource -resize "${size}x${size}" "$IconsDir\icon_$size.png"
        }
        Write-Host "  Generated PNG icons from SVG"
    }
    else {
        Write-Host "Error: No source image found" -ForegroundColor Red
        Write-Host "Need either $SourcePng or $SvgSource" -ForegroundColor Yellow
        exit 1
    }
}

# Generate .ico file with multiple sizes
Write-Host "  Creating $AppName.ico..."

# Collect existing icon sizes
$IconFiles = @()
$sizes = @(256, 128, 64, 48, 32, 16)
foreach ($size in $sizes) {
    $pngFile = "$IconsDir\icon_$size.png"
    if (Test-Path $pngFile) {
        $IconFiles += $pngFile
    }
}

if ($IconFiles.Count -eq 0) {
    Write-Host "Error: No icon PNG files found" -ForegroundColor Red
    exit 1
}

# Create ICO with all sizes
$iconArgs = $IconFiles + @("-define", "icon:auto-resize=256,128,64,48,32,16", "$AppName.ico")
& magick convert @iconArgs

if (Test-Path "$AppName.ico") {
    $fileSize = (Get-Item "$AppName.ico").Length
    Write-Host ""
    Write-Host "Icon generated successfully!" -ForegroundColor Green
    Write-Host "  Output: $AppName.ico ($fileSize bytes)"
}
else {
    Write-Host "Error: Failed to create .ico file" -ForegroundColor Red
    exit 1
}
