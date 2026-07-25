# Polymarket Conservative Trader

Risk-first, paper-first trading system for Polymarket. It scans liquid binary
markets, looks for mechanically verifiable pricing edges, and refuses to trade
when the expected edge is too small.

> This software cannot guarantee profit. Prediction-market positions can lose
> their full value. Live trading is deliberately locked by default.

## What v0.1 does

- Fetches active markets from the public Gamma API.
- Normalizes binary YES/NO markets and filters weak liquidity.
- Batch-fetches actual CLOB books and requires two-sided executable liquidity.
- Detects complete-set arbitrage after configurable costs.
- Detects conservative near-resolution convergence candidates.
- Sizes positions as a percentage of current equity.
- Applies per-trade, portfolio, daily-loss, drawdown and correlation limits.
- Applies a conservative slippage buffer and records paper order candidates.
- Persists scanner decisions and candidates to SQLite for auditability.
- Requires multiple explicit gates before live execution can be added/enabled.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
polybot scan
polybot run --once
pytest
```

The initial paper balance is `$300`. Change it in `.env`; do not change the risk
limits until paper results justify doing so.

## Safety model

Default sizing risks at most 1% of equity on a directional idea, caps one market
at 5%, caps total open exposure at 25%, stops new entries after a 2% daily loss,
and halts at an 8% peak-to-trough drawdown. Profits compound automatically
because every limit is calculated from current realized equity.

Live mode is intentionally not implemented in v0.1. A reliable live adapter
requires wallet-specific signature type, funder address, API credentials,
allowances, heartbeat cancellation and reconciliation. Paper evidence comes
first.

## Validation gates before tiny-live

1. At least 30 calendar days of uninterrupted paper operation.
2. At least 100 simulated fills across more than 20 independent markets.
3. Positive net expectancy after fees and conservative slippage.
4. Maximum drawdown below 8%.
5. No unresolved accounting or stale-order reconciliation errors.
6. Manual review of market-resolution rules for any directional strategy.

See [docs/OPERATOR.md](docs/OPERATOR.md) and [docs/STRATEGY.md](docs/STRATEGY.md).

Windows users should follow [docs/WINDOWS.md](docs/WINDOWS.md). The included
PowerShell scripts create the virtual environment, validate the installation,
run paper cycles and optionally install a five-minute Windows Scheduled Task.
