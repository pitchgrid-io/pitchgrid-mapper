# Development startup script - Desktop app with hot reload (Windows / PowerShell)
# Mirrors run_dev.sh.

$ErrorActionPreference = "Stop"

Write-Host "=== PitchGrid Mapper Development Mode ===" -ForegroundColor Cyan
Write-Host ""

# Check prerequisites
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "Error: uv is not installed" -ForegroundColor Red
    Write-Host "Install with: irm https://astral.sh/uv/install.ps1 | iex" -ForegroundColor Yellow
    exit 1
}
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Write-Host "Error: npm is not installed" -ForegroundColor Red
    exit 1
}

# Warn if Python isn't 3.12. Do NOT use `2>&1` on native commands in
# Windows PowerShell 5.1 — it wraps each stderr line as an ErrorRecord and
# combined with $ErrorActionPreference="Stop" that aborts the script just
# because uv printed a deprecation warning to stderr.
$pythonVersion = uv run python --version
if ($pythonVersion -notmatch "3\.12") {
    Write-Host "Warning: Python 3.12 is required (found: $pythonVersion)" -ForegroundColor Yellow
}

Write-Host "Syncing Python dependencies (upgrading scalatrix to latest)..." -ForegroundColor Yellow
uv sync --upgrade-package scalatrix

if (-not (Test-Path "frontend/node_modules")) {
    Write-Host ""
    Write-Host "Installing frontend dependencies..." -ForegroundColor Yellow
    Push-Location frontend
    npm install
    Pop-Location
}

$BackendPortFile = Join-Path $PWD ".dev_backend_port"
if (Test-Path $BackendPortFile) { Remove-Item $BackendPortFile -Force }

$BackendProc = $null
$FrontendProc = $null

function Stop-ProcessTree {
    param([int]$ProcessId)
    # Kill descendants first (depth-first) so we don't strand grandchildren when
    # an intermediate parent dies. `uv run python` and `cmd /c npm run dev` both
    # produce multi-level trees that Stop-Process alone won't clean up.
    Get-CimInstance Win32_Process -Filter "ParentProcessId=$ProcessId" -ErrorAction SilentlyContinue |
        ForEach-Object { Stop-ProcessTree -ProcessId $_.ProcessId }
    try { Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue } catch {}
}

function Stop-DevProcesses {
    if ($BackendProc) {
        Stop-ProcessTree -ProcessId $BackendProc.Id
    }
    if ($FrontendProc) {
        Stop-ProcessTree -ProcessId $FrontendProc.Id
    }
    if (Test-Path $BackendPortFile) { Remove-Item $BackendPortFile -Force -ErrorAction SilentlyContinue }
}

try {
    Write-Host ""
    Write-Host "Starting Desktop App with Hot Reload..." -ForegroundColor Cyan
    Write-Host "Virtual MIDI Device: PitchGrid Mapper"
    Write-Host "Press Ctrl+C to stop all services"
    Write-Host ""

    # Pick a free port for the frontend dev server
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    $listener.Start()
    $FrontendPort = $listener.LocalEndpoint.Port
    $listener.Stop()

    Write-Host "Starting backend..." -ForegroundColor Yellow
    $env:PGISOMAP_DEBUG = "true"
    $env:PGISOMAP_PORT_FILE = $BackendPortFile
    # Don't set PGISOMAP_WEB_PORT — let the OS assign an ephemeral port
    $BackendProc = Start-Process -FilePath "uv" -ArgumentList @("run", "python", "-m", "pg_isomap") -PassThru -NoNewWindow

    Write-Host "Waiting for backend to start..."
    $waitCount = 0
    while (-not (Test-Path $BackendPortFile) -and -not $BackendProc.HasExited) {
        Start-Sleep -Milliseconds 200
        $waitCount++
        if ($waitCount -ge 50) {
            throw "Backend failed to start within timeout"
        }
    }
    if (-not (Test-Path $BackendPortFile)) {
        throw "Backend process exited before becoming ready"
    }

    $BackendPort = (Get-Content $BackendPortFile -Raw).Trim()

    Write-Host ""
    Write-Host "=====================================" -ForegroundColor Green
    Write-Host "  Backend API:  http://localhost:$BackendPort"
    Write-Host "  Frontend Dev: http://localhost:$FrontendPort"
    Write-Host "=====================================" -ForegroundColor Green
    Write-Host ""

    Write-Host "Starting frontend dev server..." -ForegroundColor Yellow
    $env:BACKEND_PORT = $BackendPort
    $env:FRONTEND_PORT = $FrontendPort
    # npm on Windows is npm.cmd (a batch file); Start-Process can't launch
    # .cmd directly, so we go through cmd.exe. Stop-ProcessTree below walks
    # descendants so cmd → npm.cmd → node all get cleaned up.
    $FrontendProc = Start-Process -FilePath "cmd.exe" -ArgumentList @("/c", "npm", "run", "dev") -WorkingDirectory (Join-Path $PWD "frontend") -PassThru -NoNewWindow

    Start-Sleep -Seconds 2

    Write-Host "Opening desktop app window..." -ForegroundColor Yellow
    $env:PGISOMAP_DEV_MODE = "true"
    $pyScript = @"
import webview

url = 'http://localhost:$FrontendPort'

window = webview.create_window(
    title='PitchGrid Mapper (Dev)',
    url=url,
    width=1280,
    height=800,
    resizable=True,
    min_size=(800, 600),
)

webview.start(debug=True)
print('Desktop app closed')
"@
    uv run python -c $pyScript
}
finally {
    Write-Host ""
    Write-Host "Stopping services..." -ForegroundColor Yellow
    Stop-DevProcesses
    Write-Host "All services stopped" -ForegroundColor Green
}
