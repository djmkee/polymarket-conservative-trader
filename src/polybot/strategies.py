from datetime import UTC, datetime, timedelta
from decimal import Decimal

from .models import Market, MarketGroup, Side, Signal


class CompleteSetArbitrage:
    """Buy YES and NO only when their combined executable cost is below $1."""

    name = "complete_set_arbitrage"

    def evaluate(self, market: Market, cost_buffer: Decimal) -> list[Signal]:
        total = market.yes_ask + market.no_ask + cost_buffer
        edge = Decimal(1) - total
        if edge <= 0:
            return []
        confidence = min(Decimal("0.999"), Decimal("0.90") + edge)
        return [
            Signal(
                strategy=self.name,
                condition_id=market.condition_id,
                token_id=token,
                side=Side.BUY,
                price=price,
                edge=edge,
                confidence=confidence,
                max_loss_per_share=total,
                rationale=f"Complete set costs {total:.4f}; locked gross payout is 1.0000.",
            )
            for token, price in ((market.yes_token, market.yes_ask), (market.no_token, market.no_ask))
        ]


class NearResolutionCandidate:
    """Produces review candidates, not automatic directional orders.

    Price alone is not evidence. This strategy stays disabled for execution
    until an independent, source-specific evidence adapter is implemented.
    """

    name = "near_resolution_review"

    def evaluate(self, market: Market) -> list[Signal]:
        now = datetime.now(UTC)
        if not market.end_time or not (now <= market.end_time <= now + timedelta(hours=24)):
            return []
        winner = max(
            ((market.yes_token, market.yes_ask), (market.no_token, market.no_ask)),
            key=lambda item: item[1],
        )
        if winner[1] < Decimal("0.97"):
            return []
        return [
            Signal(
                strategy=self.name,
                condition_id=market.condition_id,
                token_id=winner[0],
                side=Side.BUY,
                price=winner[1],
                edge=Decimal(0),
                confidence=winner[1],
                max_loss_per_share=winner[1],
                rationale="Manual evidence review required; market price is not independent evidence.",
            )
        ]


class NegativeRiskArbitrage:
    """Buy every YES outcome in a complete mutually-exclusive event."""

    name = "negative_risk_complete_set"

    @staticmethod
    def basket_edge(group: MarketGroup, cost_per_leg: Decimal) -> Decimal:
        total = sum((market.yes_ask for market in group.markets), Decimal(0))
        total += cost_per_leg * len(group.markets)
        return Decimal(1) - total

    def evaluate(self, group: MarketGroup, cost_per_leg: Decimal) -> list[Signal]:
        total = sum((market.yes_ask for market in group.markets), Decimal(0))
        total += cost_per_leg * len(group.markets)
        edge = self.basket_edge(group, cost_per_leg)
        if edge <= 0:
            return []
        return [
            Signal(
                strategy=self.name,
                condition_id=market.condition_id,
                token_id=market.yes_token,
                side=Side.BUY,
                price=market.yes_ask,
                edge=edge,
                confidence=Decimal("0.95"),
                max_loss_per_share=total,
                rationale=f"Complete {len(group.markets)}-outcome basket costs {total:.4f}.",
            )
            for market in group.markets
        ]
