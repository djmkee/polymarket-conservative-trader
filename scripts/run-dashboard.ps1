$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not (Test-Path ".venv\Scripts\polybot.exe")) {
    throw "Run .\scripts\setup-windows.ps1 first."
}

Write-Host "Starting local paper dashboard at http://127.0.0.1:8765"
Write-Host "Press Ctrl+C to stop the dashboard and real-time paper engine."
& .\.venv\Scripts\polybot.exe dashboard
