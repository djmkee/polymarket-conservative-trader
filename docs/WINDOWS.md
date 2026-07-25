# Windows setup

## Install

1. Install Git for Windows.
2. Install Python 3.11 or newer from python.org. Enable **Add Python to PATH**.
3. Open PowerShell and run:

```powershell
git clone https://github.com/djmkee/polymarket-conservative-trader.git
cd polymarket-conservative-trader
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
.\scripts\setup-windows.ps1
```

## Run one paper cycle

```powershell
.\scripts\run-paper.ps1
```

## Run automatically

This installs a Windows Scheduled Task that runs one isolated paper cycle every
five minutes. It does not enable live trading.

```powershell
.\scripts\install-scheduled-task.ps1
```

Remove it with:

```powershell
.\scripts\uninstall-scheduled-task.ps1
```

## Files that must remain private

Never commit or share:

- `.env`
- `polybot.sqlite3`
- wallet private keys or seed phrases
- CLOB API key, secret or passphrase
- Telegram bot token

The repository's `.gitignore` excludes `.env` and SQLite databases.
