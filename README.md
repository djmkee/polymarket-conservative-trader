# Polymarket Conservative Trader

Risk-first, paper-first trading system for Polymarket. It scans liquid binary
markets, looks for mechanically verifiable pricing edges, and refuses to trade
when the expected edge is too small.

> This software cannot guarantee profit. Prediction-market positions can lose
> their full value. Live trading is deliberately locked by default.

## What the current paper release does

- Fetches active markets from the public Gamma API.
- Normalizes binary YES/NO markets and filters weak liquidity.
- Batch-fetches actual CLOB books and requires two-sided executable liquidity.
- Detects complete-set arbitrage after configurable costs.
- Checks complete 3–20 outcome negative-risk events for basket arbitrage.
- Runs a persistent post-only market-making simulator across up to three markets.
- Runs an event-driven public WebSocket paper engine with automatic reconnects.
- Quotes paired BUY-YES/BUY-NO orders whose combined target cost preserves an edge.
- Compounds maker order size from 2% of current equity, with minimum-order,
  per-market, total-capital and unmatched-directional caps.
- Skews prices toward neutral inventory and pauses markets after abrupt midpoint moves.
- Rejects stale books, tries to hedge one-leg fills after 15 seconds and forces
  unmatched paper positions flat after 60 seconds.
- Ranks reward-eligible markets alongside spread while refusing unaffordable reward sizes.
- Seeds balanced YES/NO complete sets and tracks cash, inventory, quotes and fills.
- Counts a paper fill only after a later executable book moves through the quote.
- Tracks directional cost basis and exits profitable excess inventory before resolution.
- Prefers fee-free maker exits; immediate exits must clear worst-case fees and slippage.
- Detects conservative near-resolution convergence candidates.
- Sizes positions as a percentage of current equity.
- Applies per-trade, portfolio, daily-loss, drawdown and correlation limits.
- Applies a conservative slippage buffer and records paper order candidates.
- Persists scanner decisions and candidates to SQLite for auditability.
- Includes a local browser dashboard for equity, P&L, positions, quotes, fills
  and confirmed manual paper-position closes, including separate completed-pair
  and directional-residual P&L.
- Reports the closest observed basket edge, including rejected negative edges.
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

True arbitrage candidates use 1% of current equity per leg. Maker orders target
2% of current marked equity in complete-set shares, cap one market at 5%, cap
maker capital at 20%, cap unmatched directional cost at 2%, and cap total open
exposure at 25%. The engine stops new entries after a 2% daily loss and halts at
an 8% peak-to-trough drawdown. Percentage sizing compounds upward after profits
and downward after losses; the five-share exchange/minimum-order floor still
applies.

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
run paper cycles, start the real-time paper engine and optionally install a
five-minute Windows Scheduled Task.

The market-making simulator is deliberately pessimistic. Touching a quote is
not treated as a fill because another maker may be ahead in the queue.

For the easiest and most realistic test, run `.\scripts\run-dashboard.ps1`.
It starts both the real-time paper engine and a local dashboard at
`http://127.0.0.1:8765`. Do not run the five-minute Scheduled Task or the
standalone real-time runner at the same time.
