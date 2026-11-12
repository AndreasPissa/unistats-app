[CmdletBinding()]
param(
    [string]$BindAddress = "127.0.0.1",
    [ValidateRange(1, 65535)][int]$Port = 8000,
    [switch]$Install
)
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
$appPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $appPython)) {
    $basePython = $env:UNISTATS_PYTHON
    if (-not $basePython) {
        $anacondaPython = Join-Path $env:USERPROFILE "anaconda3\python.exe"
        if (Test-Path -LiteralPath $anacondaPython) { $basePython = $anacondaPython }
    }
    if (-not $basePython) {
        $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if ($pythonCommand -and $pythonCommand.Source -notlike "*WindowsApps*") {
            $basePython = $pythonCommand.Source
        }
    }
    if ($basePython) {
        & $basePython -m venv .venv
    } elseif (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3 -m venv .venv
    } else {
        throw "Python 3.11 or newer is required. Install Python or set UNISTATS_PYTHON to its executable path."
    }
    if ($LASTEXITCODE -ne 0) { throw "Could not create the Python environment." }
}

$dependencyMarker = Join-Path $PSScriptRoot ".venv\.unistats-requirements"
$requirementsHash = (Get-FileHash -LiteralPath "requirements.txt" -Algorithm SHA256).Hash
$installedHash = if (Test-Path -LiteralPath $dependencyMarker) { Get-Content -LiteralPath $dependencyMarker -Raw } else { "" }
if ($Install -or $installedHash.Trim() -ne $requirementsHash) {
    & $appPython -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
    Set-Content -LiteralPath $dependencyMarker -Value $requirementsHash -NoNewline
}

Write-Host 'Starting UniStats...'
Write-Host ""
Write-Host "UniStats: http://127.0.0.1:$Port" -ForegroundColor Cyan
Write-Host "Press Ctrl+C to stop. The first data read may take a few seconds."
& $appPython -B -m uvicorn backend.main:app --host $BindAddress --port $Port
exit $LASTEXITCODE
