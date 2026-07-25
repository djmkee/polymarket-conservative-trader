from datetime import UTC, datetime, timedelta
from decimal import Decimal

from polybot.models import Market
from polybot.realtime import LiveBookCache


def market() -> Market:
    return Market(
        condition_id="condition",
        question="Test?",
        yes_token="yes",
        no_token="no",
        yes_ask=Decimal(".44"),
        no_ask=Decimal(".60"),
        yes_bid=Decimal(".40"),
        no_bid=Decimal(".56"),
        liquidity=Decimal(100_000),
        end_time=datetime.now(UTC) + timedelta(days=10),
    )


def test_book_cache_applies_snapshot_and_price_change():
    cache = LiveBookCache([market()])

    snapshot = cache.apply(
        {
            "event_type": "book",
            "asset_id": "yes",
            "bids": [{"price": ".41", "size": "50"}],
            "asks": [{"price": ".43", "size": "70"}],
        }
    )
    changed = cache.apply(
        {
            "event_type": "price_change",
            "price_changes": [
                {"asset_id": "yes", "best_bid": ".42", "best_ask": ".44"}
            ],
        }
    )

    updated = cache.markets()[0]
    assert snapshot[0]["bid"] == "0.41"
    assert changed[0]["event_type"] == "price_change"
    assert updated.yes_bid == Decimal(".42")
    assert updated.yes_ask == Decimal(".44")
    assert updated.yes_bid_size == Decimal(50)
