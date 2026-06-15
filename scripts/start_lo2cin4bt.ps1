param(
    [int]$Port = 2424,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"

Set-Location $RepoRoot
$Host.UI.RawUI.WindowTitle = "lo2cin4bt"

Write-Host "Starting lo2cin4bt..." -ForegroundColor Yellow
Write-Host "URL: http://127.0.0.1:$Port/" -ForegroundColor Cyan
Write-Host "Keep this window open while using the app." -ForegroundColor Gray

$escapedVenvPython = [regex]::Escape($VenvPython)
$stalePythonProcesses = Get-CimInstance Win32_Process |
    Where-Object {
        $_.CommandLine -and
        $_.CommandLine -match $escapedVenvPython -and
        $_.CommandLine -match "main\.py"
    }

foreach ($process in $stalePythonProcesses) {
    Get-CimInstance Win32_Process |
        Where-Object { $_.ParentProcessId -eq $process.ProcessId } |
        ForEach-Object {
            Write-Host "Stopping previous lo2cin4bt child process $($_.ProcessId)..." -ForegroundColor DarkYellow
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }
    Write-Host "Stopping previous lo2cin4bt process $($process.ProcessId)..." -ForegroundColor DarkYellow
    Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
}

if (-not (Test-Path $VenvPython)) {
    Write-Host "lo2cin4bt is not installed yet. Run .\scripts\setup.ps1 first." -ForegroundColor Yellow
    Read-Host "Press Enter to close"
    exit 1
}

$arguments = @("main.py", "--port", "$Port")
if ($NoBrowser) {
    $arguments += "--no-browser"
}

& $VenvPython @arguments
if ($LASTEXITCODE -ne 0) {
    Write-Host "lo2cin4bt stopped with exit code $LASTEXITCODE." -ForegroundColor Red
    Read-Host "Press Enter to close"
}
