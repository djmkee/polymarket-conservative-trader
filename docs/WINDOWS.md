# Windows setup

## Install

1. Install Git for Windows.
2. Install Python 3.11 or newer from python.org. Enable **Add Python to PATH**.
   Alternatively, run `winget install --id Python.Python.3.12 -e`, then reopen
   PowerShell.
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

Inspect the persistent observation history at any time:

```powershell
.\scripts\status.ps1
```

## Run the recommended real-time paper test

Stop the five-minute task first if it is installed, then start the dashboard:

```powershell
.\scripts\uninstall-scheduled-task.ps1
.\scripts\run-dashboard.ps1
```

Your browser opens `http://127.0.0.1:8765`. Leave the PowerShell window open.
The dashboard shows cash, marked equity, realized and unrealized P&L,
directional and balanced inventory, open quotes, fills and the bot's live event
journal. It also shows whether sizing compounds, the active equity percentage
and the hedge/forced-exit timers. Completed-pair P&L is separated from
directional-residual P&L so an apparently profitable total cannot hide one-leg
losses. A directional paper position can be manually closed at the latest
recorded executable bid after a confirmation prompt.

The engine maintains public order books, refreshes paper quotes every five
seconds, reconnects when the feed drops and rescans the market universe every
15 minutes. Press `Ctrl+C` in PowerShell to stop it.

The wallet panel intentionally remains read-only and disconnected while live
trading is locked. Do not enter private keys or seed phrases into the dashboard.

Do not run `run-paper.ps1`, the Scheduled Task and the real-time engine
simultaneously against the same SQLite file.

If you only want the terminal version without the dashboard:

```powershell
.\scripts\run-realtime-paper.ps1
```

## Files that must remain private

Never commit or share:

- `.env`
- `polybot.sqlite3`
- wallet private keys or seed phrases
- CLOB API key, secret or passphrase
- Telegram bot token

The repository's `.gitignore` excludes `.env` and SQLite databases.
