$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not (Test-Path ".venv\Scripts\polybot.exe")) {
    throw "Run .\scripts\setup-windows.ps1 first."
}

& .\.venv\Scripts\polybot.exe status
