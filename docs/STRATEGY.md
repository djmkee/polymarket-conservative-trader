# Strategy and return policy

The bot has no daily quota. A quota encourages bad trades. It maximizes
risk-adjusted expected value subject to hard survival constraints.

## Enabled

**Complete-set arbitrage:** Buy matching YES and NO shares only when their
executable combined cost, including fees and slippage, is below the guaranteed
$1 redemption value. Both legs must be fillable; otherwise neither is placed.

**Pair-safe paper market making:** Select up to three liquid, non-extreme,
long-dated markets with sufficient two-sided spread. Paired BUY-YES and BUY-NO
quotes must preserve the configured combined-cost edge. Depth-weighted
microprice provides the internal fair value, inventory imbalance skews the
reservation price toward neutral, and abrupt midpoint moves suspend new quotes.
Quotes expire every cycle. A fill is recognized only if a later executable book
moves strictly through the quote. Inventory and cash persist in SQLite.

When only one paired leg fills, the opposite quote is capped so that the pair
still retains the target edge. After the hedge timer expires, paper mode tries
an immediate opposite-side hedge or a controlled sale only when the estimated
loss remains inside the configured per-share cap. Filled YES/NO directional
pairs are merged at their guaranteed $1 complete-set value.

**Real-time paper feed:** The recommended Windows runner subscribes to public
market WebSockets, journals top-of-book changes, refreshes paper quotes every
five seconds and periodically rebuilds the tracked universe. The one-cycle
scanner remains available as a slower fallback.

Transaction count is a secondary metric. The engine may place many quotes but
does not manufacture fills or trade through a negative expected edge.

**Pre-resolution profit exits:** Directional inventory acquired by maker BUY
fills receives a persistent cost basis. The engine first posts a maker SELL
above cost. It may instead sell at the executable bid when the proceeds remain
profitable after the maximum configured taker-fee curve, slippage and the
minimum per-share profit. Balanced seed inventory is excluded from this rule.

**Manual paper exit:** The local dashboard can close all shares in a
directional paper position at the most recently recorded executable bid. The
simulated proceeds deduct the configured worst-case fee curve and slippage.
This action requires an explicit confirmation and is not a live wallet order.

## Review-only

**Near-resolution convergence:** High displayed probability is not proof. These
markets are logged for human review until a source adapter can independently
verify the outcome and interpret the written resolution criteria.

## Planned after data collection

- Cross-market logical consistency constraints.
- Queue-aware replay using the recorded real-time journal.
- External fair-value adapters with strict source matching and staleness limits.
- Source-specific evidence adapters with staleness and contradiction detection.

Backtests will use walk-forward evaluation and conservative fills. Parameters
will not be selected on the test period.
