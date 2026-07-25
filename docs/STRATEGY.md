# Strategy and return policy

The bot has no daily quota. A quota encourages bad trades. It maximizes
risk-adjusted expected value subject to hard survival constraints.

## Enabled

**Complete-set arbitrage:** Buy matching YES and NO shares only when their
executable combined cost, including fees and slippage, is below the guaranteed
$1 redemption value. Both legs must be fillable; otherwise neither is placed.

**Conservative paper market making:** Select up to three liquid, non-extreme,
long-dated markets with sufficient two-sided spread. Seed balanced YES/NO
inventory by splitting paper collateral, then maintain small post-only buy and
sell quotes. Quotes expire every cycle. A fill is recognized only if the next
executable book moves strictly through the quote. Inventory and cash persist in
SQLite across separate scheduled runs.

Transaction count is a secondary metric. The engine may place many quotes but
does not manufacture fills or trade through a negative expected edge.

**Pre-resolution profit exits:** Directional inventory acquired by maker BUY
fills receives a persistent cost basis. The engine first posts a maker SELL
above cost. It may instead sell at the executable bid when the proceeds remain
profitable after the maximum configured taker-fee curve, slippage and the
minimum per-share profit. Balanced seed inventory is excluded from this rule.

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
