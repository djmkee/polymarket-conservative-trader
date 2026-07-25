from dataclasses import replace
from decimal import Decimal

from polybot.models import MarketGroup
from polybot.strategies import NegativeRiskArbitrage
from tests.test_strategy import market


def group(prices: list[str]) -> MarketGroup:
    members = tuple(
        replace(
            market(price, str(Decimal(1) - Decimal(price))),
            condition_id=f"c-{index}",
            yes_token=f"yes-{index}",
        )
        for index, price in enumerate(prices)
    )
    return MarketGroup(event_id="e", title="Winner", neg_risk_id="n", markets=members)


def test_negative_risk_requires_net_basket_edge():
    strategy = NegativeRiskArbitrage()
    signals = strategy.evaluate(group([".20", ".30", ".40"]), Decimal(".005"))
    assert len(signals) == 3
    assert signals[0].edge == Decimal(".085")


def test_negative_risk_rejects_full_price_basket():
    assert NegativeRiskArbitrage().evaluate(
        group([".20", ".30", ".50"]), Decimal(".005")
    ) == []
