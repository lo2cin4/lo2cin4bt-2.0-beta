param(
    [string]$Name = "lo2cin4bt",
    [int]$Port = 2424,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$StartScript = Join-Path $RepoRoot "scripts\start_lo2cin4bt.ps1"
$IconPath = Join-Path $RepoRoot "assets\desktop\lo2cin4bt-logo.ico"
$Desktop = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $Desktop "$Name.lnk"
$PowerShellExe = Join-Path $PSHOME "powershell.exe"

if (-not (Test-Path $StartScript)) {
    throw "Missing start script: $StartScript"
}

$arguments = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", "`"$StartScript`"",
    "-Port", "$Port"
)

if ($NoBrowser) {
    $arguments += "-NoBrowser"
}

$shell = New-Object -ComObject WScript.Shell
if (Test-Path $ShortcutPath) {
    Remove-Item -LiteralPath $ShortcutPath -Force
}
$shortcut = $shell.CreateShortcut($ShortcutPath)
$shortcut.TargetPath = $PowerShellExe
$shortcut.Arguments = $arguments -join " "
$shortcut.WorkingDirectory = $RepoRoot
$shortcut.Description = "Start lo2cin4bt local backtesting app"
if (Test-Path $IconPath) {
    $shortcut.IconLocation = $IconPath
}
$shortcut.Save()

Write-Host "Created desktop shortcut: $ShortcutPath"
