# Prints the project version from pyproject.toml.
# Single source of truth for build/release scripts — used instead of any
# APP_VERSION that might leak in from the environment.

$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$pyproject = Join-Path $scriptDir '..\pyproject.toml'

if (-not (Test-Path $pyproject)) {
    throw "pyproject.toml not found at $pyproject"
}

$inProject = $false
foreach ($line in Get-Content $pyproject) {
    if ($line -match '^\s*\[project\]\s*$') { $inProject = $true; continue }
    if ($line -match '^\s*\[' -and -not ($line -match '^\s*\[project\]\s*$')) { $inProject = $false; continue }
    if ($inProject -and $line -match '^\s*version\s*=\s*"([^"]+)"') {
        Write-Output $Matches[1]
        return
    }
}

throw "version not found in [project] section of $pyproject"
