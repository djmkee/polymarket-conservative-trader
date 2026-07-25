# Strategy and return policy

The bot has no daily quota. A quota encourages bad trades. It maximizes
risk-adjusted expected value subject to hard survival constraints.

## Enabled

**Complete-set arbitrage:** Buy matching YES and NO shares only when their
executable combined cost, including fees and slippage, is below the guaranteed
$1 redemption value. Both legs must be fillable; otherwise neither is placed.

## Review-only

**Near-resolution convergence:** High displayed probability is not proof. These
markets are logged for human review until a source adapter can independently
verify the outcome and interpret the written resolution criteria.

## Planned after data collection

- Cross-market logical consistency constraints.
- Inventory-aware passive quoting.
- Maker reward optimization without adverse-selection exposure.
- Source-specific evidence adapters with staleness and contradiction detection.

Backtests will use walk-forward evaluation and conservative fills. Parameters
will not be selected on the test period.
