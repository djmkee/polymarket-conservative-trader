# Strategy and return policy

The bot has no daily quota. A quota encourages bad trades. It maximizes
risk-adjusted expected value subject to hard survival constraints.

## Enabled

**Complete-set arbitrage:** Buy matching YES and NO shares only when their
executable combined cost, including fees and slippage, is below the guaranteed
$1 redemption value. Both legs must be fillable; otherwise neither is placed.

**Pair-safe paper market making:** Select up to three liquid, non-extreme,
markets ending in 6 hours–30 days with sufficient two-sided spread. Paired
BUY-YES and BUY-NO
quotes must preserve the configured combined-cost edge. Depth-weighted
microprice provides the internal fair value, inventory imbalance skews the
reservation price toward neutral, and abrupt midpoint moves suspend new quotes.
Unchanged quotes remain resting across cycles so their time and queue position
are not discarded every five seconds. A paper fill requires either a later
executable book to move strictly through the quote or public
`last_trade_price` volume to consume the displayed queue ahead plus the bot's
order size. Merely touching the best bid does not count as a fill. On a
one-tick spread the bot may join the current best bid, but it records the
displayed size ahead of the paper order and waits for enough opposite-side
trading volume. Inventory and cash persist in SQLite.

Order size compounds from current marked equity. The default target is 2% of
equity expressed as complete-set shares, subject to the five-share/minimum-order
floor and the 5% per-market cap. Total maker capital is capped at 20% of current
equity and any unmatched directional cost basis is capped at 2%. At $300 the
default target is six shares; at $600 it is twelve. Fixed-size mode remains
available by setting `POLYBOT_MAKER_COMPOUND=false`.

When only one paired leg fills, the opposite quote is capped so that the pair
still retains the target edge. After the 15-second hedge timer expires, paper
mode tries an immediate opposite-side hedge or a controlled sale when the
estimated loss remains inside the configured per-share cap. An unmatched leg is
forcibly flattened after 60 seconds rather than silently becoming a prediction
bet. Filled YES/NO directional pairs are merged at their guaranteed $1
complete-set value.

The engine does not seed complete sets. Any balanced non-directional inventory
left by an older paper release is automatically merged back to paper cash on
the next cycle. This prevents the dashboard from showing outcome-neutral money
as locked until a distant resolution date.

**Real-time paper feed:** The recommended Windows runner subscribes to public
market WebSockets, journals top-of-book changes, refreshes paper quotes every
five seconds and periodically rebuilds the tracked universe. New quotes and
simulated fills are rejected when either side of the cached book is older than
30 seconds. The one-cycle scanner remains available as a slower fallback.

Transaction count is a secondary metric. The engine may place many quotes but
does not manufacture fills or trade through a negative expected edge.

**Pre-resolution profit exits:** Directional inventory acquired by maker BUY
fills receives a persistent cost basis. The engine first posts a maker SELL
above cost. It may instead sell at the executable bid when the proceeds remain
profitable after the maximum configured taker-fee curve, slippage and the
minimum per-share profit.

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

The external-sportsbook strategy is not enabled without a fresh, independently
verified odds feed. Historical evidence shows that completed paired arbitrage
can be profitable while stale quotes and unmatched legs can erase much of that
profit. Five-minute crypto markets also remain disabled: a historical
second-by-second dataset is useful for research, but its publisher reported a
positive backtest followed by a live loss after fees.
