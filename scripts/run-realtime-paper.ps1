$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not (Test-Path ".venv\Scripts\polybot.exe")) {
    throw "Run .\scripts\setup-windows.ps1 first."
}

Write-Host "Starting real-time PAPER mode. Press Ctrl+C to stop."
& .\.venv\Scripts\polybot.exe stream
