from datetime import UTC, datetime, timedelta
from decimal import Decimal

from polybot.config import Settings
from polybot.market_maker import PaperMarketMaker
from polybot.models import Market
from polybot.store import AuditStore


def book(
    yes_bid: str = ".40",
    yes_ask: str = ".44",
    no_bid: str = ".56",
    no_ask: str = ".60",
) -> Market:
    return Market(
        condition_id="condition",
        question="Test market?",
        yes_token="yes",
        no_token="no",
        yes_ask=Decimal(yes_ask),
        no_ask=Decimal(no_ask),
        yes_bid=Decimal(yes_bid),
        no_bid=Decimal(no_bid),
        liquidity=Decimal(100_000),
        end_time=datetime.now(UTC) + timedelta(days=30),
        yes_ask_size=Decimal(100),
        no_ask_size=Decimal(100),
        min_order_size=Decimal(5),
        tick_size=Decimal(".01"),
    )


def test_maker_seeds_quotes_and_marks_complete_set(tmp_path):
    store = AuditStore(tmp_path / "paper.sqlite3")
    maker = PaperMarketMaker(Settings(maker_max_markets=1), store)
    result = maker.run([book()])

    assert result["maker_seeded"] == 1
    assert result["maker_quotes"] == 4
    assert result["paper_equity"] == "300.0000"
    assert store.paper_summary()["cash"] == "295"
    store.close()


def test_maker_only_fills_after_book_moves_through_quote(tmp_path):
    store = AuditStore(tmp_path / "paper.sqlite3")
    maker = PaperMarketMaker(Settings(maker_max_markets=1), store)
    maker.run([book()])

    result = maker.run([book(yes_bid=".38", yes_ask=".40", no_bid=".61", no_ask=".63")])

    assert result["maker_fills"] == 2
    assert store.paper_summary()["fills"] == 2
    store.close()


def test_maker_does_not_seed_replacement_beyond_market_cap(tmp_path):
    store = AuditStore(tmp_path / "paper.sqlite3")
    maker = PaperMarketMaker(Settings(maker_max_markets=1), store)
    maker.run([book()])
    replacement = Market(
        **{
            **book().__dict__,
            "condition_id": "replacement",
            "yes_token": "replacement-yes",
            "no_token": "replacement-no",
        }
    )

    result = maker.run([replacement])

    assert result["maker_seeded"] == 0
    assert store.paper_summary()["cash"] == "295"
    store.close()
