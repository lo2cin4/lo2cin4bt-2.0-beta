param(
    [switch]$Dev,
    [switch]$Brokers,
    [switch]$SkipFrontend
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"

function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$Arguments
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $FilePath $($Arguments -join ' ')"
    }
}

Set-Location $RepoRoot

if (-not (Test-Path $VenvPython)) {
    Invoke-Native python -m venv .venv
}

Invoke-Native $VenvPython -m pip install -q --disable-pip-version-check --upgrade pip wheel setuptools
Invoke-Native $VenvPython -m pip install -q --disable-pip-version-check --require-hashes -r requirements.lock

if ($Dev) {
    Invoke-Native $VenvPython -m pip install -q --disable-pip-version-check --require-hashes -r requirements-dev.lock
}

if ($Brokers) {
    Invoke-Native $VenvPython -m pip install -q --disable-pip-version-check -r requirements-brokers.txt
}

if (-not $SkipFrontend) {
    Push-Location plotter/web
    try {
        Invoke-Native npm ci
        Invoke-Native npm run build
    }
    finally {
        Pop-Location
    }
}

Invoke-Native $VenvPython scripts\doctor.py
