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
    snapshot_time = datetime.now(UTC)

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
    assert updated.yes_updated_at >= snapshot_time


def test_healthy_stream_keeps_unchanged_books_fresh():
    stale = datetime.now(UTC) - timedelta(minutes=2)
    cache = LiveBookCache(
        [
            Market(
                **{
                    **market().__dict__,
                    "yes_updated_at": stale,
                    "no_updated_at": stale,
                }
            )
        ]
    )
    observed_at = datetime.now(UTC)

    cache.mark_stream_alive(observed_at)

    refreshed = cache.markets()[0]
    assert refreshed.yes_updated_at == observed_at
    assert refreshed.no_updated_at == observed_at


def test_book_cache_emits_public_trade_without_moving_the_book():
    cache = LiveBookCache([market()])

    trades = cache.apply(
        {
            "event_type": "last_trade_price",
            "asset_id": "yes",
            "price": ".40",
            "size": "25",
            "side": "SELL",
            "timestamp": "1782753357257",
            "transaction_hash": "0xtrade",
        }
    )

    assert trades == [
        {
            "condition_id": "condition",
            "token_id": "yes",
            "price": "0.40",
            "size": "25",
            "side": "SELL",
            "event_type": "last_trade_price",
            "transaction_hash": "0xtrade",
            "occurred_at": "1782753357257",
        }
    ]
    assert cache.markets()[0].yes_bid == Decimal(".40")
