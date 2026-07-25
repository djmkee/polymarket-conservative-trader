from datetime import UTC, datetime, timedelta
from decimal import Decimal

from polybot.models import Market
from polybot.strategies import CompleteSetArbitrage, NearResolutionCandidate


def market(yes: str, no: str) -> Market:
    return Market(
        condition_id="c",
        question="Test?",
        yes_token="y",
        no_token="n",
        yes_ask=Decimal(yes),
        no_ask=Decimal(no),
        yes_bid=Decimal(yes) - Decimal(".01"),
        no_bid=Decimal(no) - Decimal(".01"),
        liquidity=Decimal(20000),
        end_time=datetime.now(UTC) + timedelta(hours=3),
    )


def test_arbitrage_requires_positive_edge_after_costs():
    strategy = CompleteSetArbitrage()
    assert len(strategy.evaluate(market(".48", ".50"), Decimal(".005"))) == 2
    assert strategy.evaluate(market(".50", ".50"), Decimal(".005")) == []


def test_near_resolution_is_review_only_with_zero_edge():
    signals = NearResolutionCandidate().evaluate(market(".975", ".025"))
    assert signals and signals[0].edge == 0
