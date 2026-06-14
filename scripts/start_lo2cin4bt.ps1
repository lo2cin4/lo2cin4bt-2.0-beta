param(
    [int]$Port = 2424,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"

Set-Location $RepoRoot

if (-not (Test-Path $VenvPython)) {
    Write-Host "lo2cin4bt is not installed yet. Run .\scripts\setup.ps1 first." -ForegroundColor Yellow
    exit 1
}

$arguments = @("main.py", "--port", "$Port")
if ($NoBrowser) {
    $arguments += "--no-browser"
}

& $VenvPython @arguments
