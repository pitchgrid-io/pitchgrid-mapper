# Sign Windows installer using Azure Trusted Signing
# Usage: .\sign_installer.ps1 [installer_path]
#
# Requires:
# - Azure account with Trusted Signing configured
# - TrustedSigning PowerShell module
# - Environment variables: AZURE_ENDPOINT, AZURE_SIGNING_ACCOUNT, AZURE_CERT_PROFILE

param(
    [string]$InstallerPath
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

$AppVersion = $env:APP_VERSION
if (-not $AppVersion) { $AppVersion = "0.1.0" }

Write-Host "Signing $AppName Windows installer..." -ForegroundColor Cyan

# Find installer to sign
if (-not $InstallerPath) {
    $OutputDir = "Installers\Windows\Output"
    $InstallerFile = Get-ChildItem "$OutputDir\*.exe" -ErrorAction SilentlyContinue |
                     Sort-Object LastWriteTime -Descending |
                     Select-Object -First 1
    if ($InstallerFile) {
        $InstallerPath = $InstallerFile.FullName
    }
}

if (-not $InstallerPath -or -not (Test-Path $InstallerPath)) {
    Write-Host "Error: No installer found to sign" -ForegroundColor Red
    Write-Host "Run .\create_installer.ps1 first, or specify path: .\sign_installer.ps1 path\to\installer.exe" -ForegroundColor Yellow
    exit 1
}

# Convert to absolute path (required by sign CLI)
$InstallerPath = (Resolve-Path $InstallerPath).Path

Write-Host "  Installer: $InstallerPath"

# Check Azure Trusted Signing configuration
$AzureEndpoint = $env:AZURE_ENDPOINT
$SigningAccount = $env:AZURE_SIGNING_ACCOUNT
$CertProfile = $env:AZURE_CERT_PROFILE

if (-not $AzureEndpoint) {
    Write-Host "Error: AZURE_ENDPOINT not set in .env" -ForegroundColor Red
    Write-Host "Set your Azure Trusted Signing endpoint URL" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "For self-signed testing, use: .\sign_installer_selfsigned.ps1" -ForegroundColor Yellow
    exit 1
}

if (-not $SigningAccount) {
    # Default to same account as pitchgrid-plugin
    $SigningAccount = "pitchgrid"
    Write-Host "  Using default signing account: $SigningAccount" -ForegroundColor Yellow
}

if (-not $CertProfile) {
    # Default to same profile as pitchgrid-plugin
    $CertProfile = "pitchgrid"
    Write-Host "  Using default certificate profile: $CertProfile" -ForegroundColor Yellow
}

# Ensure dotnet is in PATH (workaround for winget PATH issue)
$dotnetPath = "C:\Program Files\dotnet"
if (Test-Path $dotnetPath) {
    if ($env:PATH -notlike "*$dotnetPath*") {
        Write-Host "Adding dotnet to PATH..." -ForegroundColor Yellow
        $env:PATH = "$dotnetPath;$env:PATH"
    }
}

# Verify dotnet is available
$dotnetCmd = Get-Command dotnet -ErrorAction SilentlyContinue
if (-not $dotnetCmd) {
    Write-Host "Error: .NET SDK not found. Please install it from https://dotnet.microsoft.com/download" -ForegroundColor Red
    exit 1
}

# Install TrustedSigning module if needed
Write-Host ""
Write-Host "Checking TrustedSigning module..." -ForegroundColor Yellow

if (-not (Get-InstalledModule TrustedSigning -ErrorAction SilentlyContinue)) {
    Write-Host "  Installing TrustedSigning module..."
    Install-Module TrustedSigning -Confirm:$False -Force -Scope CurrentUser
}
Write-Host "  TrustedSigning module ready"

# Sign the installer
Write-Host ""
Write-Host "Signing with Azure Trusted Signing..." -ForegroundColor Yellow

$params = @{
    Endpoint              = $AzureEndpoint
    CodeSigningAccountName = $SigningAccount
    CertificateProfileName = $CertProfile
    Files                 = $InstallerPath
    FileDigest            = "SHA256"
    TimestampRfc3161      = "http://timestamp.acs.microsoft.com"
    TimestampDigest       = "SHA256"
}

try {
    Invoke-TrustedSigning @params
    Write-Host ""
    Write-Host "Signing completed successfully!" -ForegroundColor Green
}
catch {
    Write-Host ""
    Write-Host "Error signing installer: $_" -ForegroundColor Red
    Write-Host ""
    Write-Host "Troubleshooting:" -ForegroundColor Yellow
    Write-Host "  1. Ensure you're logged in to Azure: az login" -ForegroundColor Yellow
    Write-Host "  2. Verify AZURE_ENDPOINT is correct" -ForegroundColor Yellow
    Write-Host "  3. Check your Azure Trusted Signing account permissions" -ForegroundColor Yellow
    exit 1
}

# Verify signature
Write-Host ""
Write-Host "Verifying signature..." -ForegroundColor Yellow

$sig = Get-AuthenticodeSignature $InstallerPath
if ($sig.Status -eq "Valid") {
    Write-Host "  Signature Status: Valid" -ForegroundColor Green
    Write-Host "  Signer: $($sig.SignerCertificate.Subject)"
    Write-Host "  Timestamp: $($sig.TimeStamperCertificate.Subject)"
}
else {
    Write-Host "  Warning: Signature status is $($sig.Status)" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Signed installer ready for distribution:" -ForegroundColor Green
Write-Host "  $InstallerPath"
Write-Host ""
Write-Host "The installer is now fully signed and ready to distribute!" -ForegroundColor Cyan
