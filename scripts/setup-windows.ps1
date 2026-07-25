$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

if (Get-Command py -ErrorAction SilentlyContinue) {
    $python = "py"
    $pythonPrefix = @("-3")
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $python = "python"
    $pythonPrefix = @()
} else {
    throw @"
Python 3.11+ was not found.

Install it with:
  winget install --id Python.Python.3.12 -e

Then close PowerShell, open a new PowerShell window, return to this folder,
and run .\scripts\setup-windows.ps1 again.
"@
}

$version = & $python @pythonPrefix -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($LASTEXITCODE -ne 0) {
    throw "Python was found but could not run. Reinstall Python 3.11+ and enable Add Python to PATH."
}
if ([version]$version -lt [version]"3.11") {
    throw "Python 3.11 or newer is required. Found $version."
}

if (-not (Test-Path ".venv")) {
    & $python @pythonPrefix -m venv .venv
}

& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -e ".[dev]"

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env with safe paper-trading defaults."
} else {
    $envText = Get-Content ".env" -Raw
    $envText = $envText -replace "(?m)^POLYBOT_MAKER_HEDGE_TIMEOUT_SECONDS=90$", "POLYBOT_MAKER_HEDGE_TIMEOUT_SECONDS=15"
    $envText = $envText -replace "(?m)^POLYBOT_MAKER_MIN_HOURS_TO_END=(48|24)$", "POLYBOT_MAKER_MIN_HOURS_TO_END=6"
    $safeAdditions = @(
        "POLYBOT_MAKER_COMPOUND=true",
        "POLYBOT_MAKER_ORDER_EQUITY_PCT=0.02",
        "POLYBOT_MAKER_MAX_CAPITAL_PCT=0.20",
        "POLYBOT_MAKER_MAX_DIRECTIONAL_EXPOSURE_PCT=0.02",
        "POLYBOT_MAKER_FORCE_FLATTEN_SECONDS=60",
        "POLYBOT_MAKER_MAX_BOOK_AGE_SECONDS=30",
        "POLYBOT_MAKER_MAX_HOURS_TO_END=720"
    )
    foreach ($setting in $safeAdditions) {
        $name = $setting.Split("=")[0]
        if ($envText -notmatch "(?m)^$([regex]::Escape($name))=") {
            $envText = $envText.TrimEnd() + "`r`n" + $setting + "`r`n"
        }
    }
    Set-Content ".env" $envText -NoNewline -Encoding ascii
    Write-Host "Updated .env with the latest safe paper-risk settings."
}

& .\.venv\Scripts\python.exe -m pytest -q
& .\.venv\Scripts\ruff.exe check .
Write-Host ""
Write-Host "Setup complete. Start with: .\scripts\run-paper.ps1"
