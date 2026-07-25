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
        yes_bid_size=Decimal(100),
        no_bid_size=Decimal(100),
        min_order_size=Decimal(5),
        tick_size=Decimal(".01"),
    )


def test_maker_seeds_quotes_and_marks_complete_set(tmp_path):
    store = AuditStore(tmp_path / "paper.sqlite3")
    maker = PaperMarketMaker(Settings(maker_max_markets=1), store)
    result = maker.run([book()])

    assert result["maker_seeded"] == 1
    assert result["maker_quotes"] == 2
    assert result["paper_equity"] == "300.0000"
    assert store.paper_summary()["cash"] == "295"
    store.close()


def test_maker_only_fills_after_book_moves_through_quote(tmp_path):
    store = AuditStore(tmp_path / "paper.sqlite3")
    maker = PaperMarketMaker(Settings(maker_max_markets=1), store)
    maker.run([book()])

    result = maker.run([book(yes_bid=".36", yes_ask=".40", no_bid=".61", no_ask=".63")])

    assert result["maker_fills"] == 1
    assert store.paper_summary()["fills"] == 1
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


def test_directional_position_exits_early_only_after_net_profit(tmp_path):
    store = AuditStore(tmp_path / "paper.sqlite3")
    maker = PaperMarketMaker(Settings(maker_max_markets=1), store)
    maker.run([book()])
    maker.run([book(yes_bid=".38", yes_ask=".40", no_bid=".61", no_ask=".63")])

    result = maker.run([book(yes_bid=".46", yes_ask=".50", no_bid=".50", no_ask=".54")])

    assert result["maker_profit_exits"] == 1
    summary = store.paper_summary()
    assert summary["fills"] == 2
    assert Decimal(summary["realized_pnl"]) > 0
    assert Decimal(summary["fees"]) > 0
    store.close()


def test_pair_quotes_preserve_combined_edge(tmp_path):
    store = AuditStore(tmp_path / "paper.sqlite3")
    settings = Settings(maker_max_markets=1, maker_pair_min_edge=Decimal(".01"))
    maker = PaperMarketMaker(settings, store)

    maker.run([book()])

    buys = [quote for quote in store.open_quotes() if quote["side"] == "BUY"]
    assert len(buys) == 2
    assert sum(Decimal(str(quote["price"])) for quote in buys) <= Decimal(".99")
    store.close()


def test_filled_pair_merges_to_cash_and_realizes_locked_edge(tmp_path):
    store = AuditStore(tmp_path / "paper.sqlite3")
    maker = PaperMarketMaker(Settings(maker_max_markets=1), store)
    maker.run([book()])

    result = maker.run(
        [book(yes_bid=".36", yes_ask=".40", no_bid=".52", no_ask=".56")]
    )

    assert result["maker_fills"] == 2
    assert result["maker_pairs_merged"] == 1
    assert Decimal(store.paper_summary()["realized_pnl"]) > 0
    assert store.directional() == {}
    store.close()


def test_expired_one_leg_uses_capped_opposite_hedge(tmp_path):
    store = AuditStore(tmp_path / "paper.sqlite3")
    settings = Settings(
        maker_max_markets=1,
        maker_hedge_timeout_seconds=10,
        maker_max_fee_rate=Decimal(0),
        maker_max_flatten_loss_per_share=Decimal(".02"),
    )
    maker = PaperMarketMaker(settings, store)
    store.adjust_inventory("yes", "condition", "Test market?", "YES", "5")
    store.add_directional_buy("yes", "5", "2")
    store.db.execute(
        "UPDATE paper_directional SET opened_at = ?",
        ((datetime.now(UTC) - timedelta(minutes=1)).isoformat(),),
    )
    store.db.commit()

    result = maker.run([book(no_ask=".60")])

    assert result["maker_hedge_exits"] == 1
    assert result["maker_pairs_merged"] == 1
    assert store.directional() == {}
    store.close()


def test_abrupt_midpoint_jump_pauses_new_market(tmp_path):
    store = AuditStore(tmp_path / "paper.sqlite3")
    maker = PaperMarketMaker(
        Settings(maker_max_markets=1, maker_max_midpoint_jump=Decimal(".03")),
        store,
    )
    store.record_tick("condition", "yes", ".35", ".39", "test")
    store.record_tick("condition", "yes", ".42", ".46", "test")

    result = maker.run([book()])

    assert result["maker_markets"] == 0
    assert result["maker_quotes"] == 0
    store.close()


def test_manual_close_uses_latest_bid_and_updates_dashboard(tmp_path):
    store = AuditStore(tmp_path / "paper.sqlite3")
    maker = PaperMarketMaker(
        Settings(maker_max_markets=1, maker_max_fee_rate=Decimal(0)),
        store,
    )
    maker.run([book()])
    maker.run([book(yes_bid=".36", yes_ask=".40", no_bid=".61", no_ask=".63")])
    store.record_tick("condition", "yes", ".45", ".49", "test")

    result = maker.manual_close("yes")
    dashboard = store.dashboard_state()

    assert Decimal(result["realized_profit"]) > 0
    assert store.directional() == {}
    assert Decimal(dashboard["account"]["realized_pnl"]) > 0
    assert dashboard["fills"][0]["side"] == "SELL"
    store.close()
