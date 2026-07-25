from decimal import Decimal

from polybot.config import Settings
from polybot.models import Portfolio, Side, Signal
from polybot.risk import RiskManager


def signal(edge: str = ".02") -> Signal:
    return Signal(
        strategy="test",
        condition_id="c",
        token_id="t",
        side=Side.BUY,
        price=Decimal(".5"),
        edge=Decimal(edge),
        confidence=Decimal(".9"),
        max_loss_per_share=Decimal(".5"),
        rationale="test",
    )


def test_compounding_size_uses_current_equity():
    manager = RiskManager(Settings())
    low = manager.size(signal(), Portfolio(Decimal(300), peak_equity=Decimal(300)))
    high = manager.size(signal(), Portfolio(Decimal(600), peak_equity=Decimal(600)))
    assert low and high and high.notional > low.notional


def test_daily_loss_halts_new_orders():
    manager = RiskManager(Settings())
    portfolio = Portfolio(
        cash=Decimal(293), peak_equity=Decimal(300), day_start_equity=Decimal(300)
    )
    assert manager.halted_reason(portfolio) == "daily_loss_limit"
    assert manager.size(signal(), portfolio) is None
