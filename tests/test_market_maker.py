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
        end_time=datetime.now(UTC) + timedelta(days=7),
        yes_ask_size=Decimal(100),
        no_ask_size=Decimal(100),
        yes_bid_size=Decimal(100),
        no_bid_size=Decimal(100),
        min_order_size=Decimal(5),
        tick_size=Decimal(".01"),
    )


def test_maker_quotes_without_locking_cash_in_seed_inventory(tmp_path):
    store = AuditStore(tmp_path / "paper.sqlite3")
    maker = PaperMarketMaker(Settings(maker_max_markets=1), store)
    result = maker.run([book()])

    assert result["maker_seeded"] == 0
    assert result["maker_balanced_redeemed"] == 0
    assert result["maker_quotes"] == 2
    assert result["paper_equity"] == "300.0000"
    assert store.paper_summary()["cash"] == "300"
    assert store.inventory() == []
    store.close()


def test_maker_only_fills_after_book_moves_through_quote(tmp_path):
    store = AuditStore(tmp_path / "paper.sqlite3")
    maker = PaperMarketMaker(Settings(maker_max_markets=1), store)
    maker.run([book()])

    result = maker.run([book(yes_bid=".36", yes_ask=".40", no_bid=".61", no_ask=".63")])

    assert result["maker_fills"] == 1
    assert store.paper_summary()["fills"] == 1
    store.close()


def test_maker_does_not_seed_replacement_inventory(tmp_path):
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
    assert store.paper_summary()["cash"] == "300"
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


def test_maker_can_join_best_bid_on_one_tick_spreads(tmp_path):
    store = AuditStore(tmp_path / "paper.sqlite3")
    maker = PaperMarketMaker(
        Settings(maker_max_markets=1, maker_min_spread=Decimal(".01")),
        store,
    )
    tight = book(
        yes_bid=".40",
        yes_ask=".41",
        no_bid=".59",
        no_ask=".60",
    )

    result = maker.run([tight])

    buys = [quote for quote in store.open_quotes() if quote["side"] == "BUY"]
    assert result["maker_markets"] == 1
    assert result["maker_quotes"] == 2
    assert {Decimal(quote["price"]) for quote in buys} == {
        Decimal(".40"),
        Decimal(".59"),
    }
    assert sum(Decimal(quote["price"]) for quote in buys) <= Decimal(".99")
    store.close()


def test_unchanged_quotes_keep_their_resting_time_and_queue(tmp_path):
    store = AuditStore(tmp_path / "paper.sqlite3")
    maker = PaperMarketMaker(
        Settings(maker_max_markets=1, maker_compound=False),
        store,
    )
    tight = book(
        yes_bid=".40",
        yes_ask=".41",
        no_bid=".59",
        no_ask=".60",
    )

    maker.run([tight])
    first = {
        (quote["token_id"], quote["side"]): (
            quote["id"],
            quote["created_at"],
            quote["queue_ahead"],
        )
        for quote in store.open_quotes()
    }
    result = maker.run([tight])
    second = {
        (quote["token_id"], quote["side"]): (
            quote["id"],
            quote["created_at"],
            quote["queue_ahead"],
        )
        for quote in store.open_quotes()
    }

    assert result["maker_fills"] == 0
    assert first == second
    assert first[("yes", "BUY")][2] == "100"
    store.close()


def test_public_sell_trade_fills_improved_resting_buy(tmp_path):
    store = AuditStore(tmp_path / "paper.sqlite3")
    maker = PaperMarketMaker(
        Settings(maker_max_markets=1, maker_compound=False),
        store,
    )
    maker.run([book()])
    yes_quote = next(
        quote
        for quote in store.open_quotes()
        if quote["token_id"] == "yes" and quote["side"] == "BUY"
    )
    assert Decimal(yes_quote["price"]) == Decimal(".41")
    assert Decimal(yes_quote["queue_ahead"]) == 0
    store.record_public_trade(
        "condition", "yes", ".41", "5", "SELL", "0xtrade"
    )

    result = maker.run([book()])

    assert result["maker_fills"] == 1
    assert store.directional()["yes"]["shares"] == "5"
    event = store.db.execute(
        "SELECT payload FROM events WHERE kind = 'paper_quote_filled'"
    ).fetchone()
    assert '"evidence": "public_trade"' in event[0]
    store.close()


def test_best_bid_quote_waits_for_queue_ahead_to_trade(tmp_path):
    store = AuditStore(tmp_path / "paper.sqlite3")
    maker = PaperMarketMaker(
        Settings(maker_max_markets=1, maker_compound=False),
        store,
    )
    tight = book(
        yes_bid=".40",
        yes_ask=".41",
        no_bid=".59",
        no_ask=".60",
    )
    maker.run([tight])
    store.record_public_trade(
        "condition", "yes", ".40", "104", "SELL", "0xpartial"
    )

    first = maker.run([tight])
    assert first["maker_fills"] == 0

    store.record_public_trade(
        "condition", "yes", ".40", "1", "SELL", "0xrest"
    )
    second = maker.run([tight])

    assert second["maker_fills"] == 1
    assert store.directional()["yes"]["shares"] == "5"
    store.close()


def test_maker_order_size_compounds_from_current_equity(tmp_path):
    store = AuditStore(tmp_path / "paper.sqlite3")
    maker = PaperMarketMaker(
        Settings(initial_equity=Decimal(600), maker_max_markets=1),
        store,
    )

    maker.run([book()])

    buys = [quote for quote in store.open_quotes() if quote["side"] == "BUY"]
    assert {Decimal(quote["size"]) for quote in buys} == {Decimal(12)}
    assert store.paper_summary()["cash"] == "600"
    store.close()


def test_maker_fixed_size_mode_remains_available(tmp_path):
    store = AuditStore(tmp_path / "paper.sqlite3")
    maker = PaperMarketMaker(
        Settings(
            initial_equity=Decimal(600),
            maker_max_markets=1,
            maker_compound=False,
        ),
        store,
    )

    maker.run([book()])

    buys = [quote for quote in store.open_quotes() if quote["side"] == "BUY"]
    assert {Decimal(quote["size"]) for quote in buys} == {Decimal(5)}
    store.close()


def test_maker_cap_includes_inventory_and_open_buy_commitments(tmp_path):
    store = AuditStore(tmp_path / "paper.sqlite3")
    settings = Settings(
        maker_max_markets=2,
        maker_max_capital_pct=Decimal(".03"),
    )
    maker = PaperMarketMaker(settings, store)
    second = Market(
        **{
            **book().__dict__,
            "condition_id": "second",
            "yes_token": "second-yes",
            "no_token": "second-no",
        }
    )

    maker.run([book(), second])

    balanced = maker._balanced_inventory_capital(store.inventory())
    committed = sum(
        (
            Decimal(quote["price"]) * Decimal(quote["size"])
            for quote in store.open_quotes()
            if quote["side"] == "BUY"
        ),
        Decimal(0),
    )
    assert balanced + committed <= Decimal(300) * Decimal(".03")
    store.close()


def test_legacy_balanced_inventory_is_redeemed_without_waiting_for_resolution(
    tmp_path,
):
    store = AuditStore(tmp_path / "paper.sqlite3")
    maker = PaperMarketMaker(Settings(maker_max_markets=1), store)
    store.adjust_inventory("yes", "condition", "Test market?", "YES", "5")
    store.adjust_inventory("no", "condition", "Test market?", "NO", "5")
    store.set_paper_account("295", "300")

    result = maker.run([book()])

    assert result["maker_balanced_redeemed"] == 1
    assert store.paper_summary()["cash"] == "300"
    assert store.inventory() == []
    event = store.db.execute(
        "SELECT payload FROM events WHERE kind = 'paper_balanced_redeemed'"
    ).fetchone()
    assert '"shares": "5"' in event[0]
    store.close()


def test_legacy_release_preserves_directional_shares_and_cost_basis(tmp_path):
    store = AuditStore(tmp_path / "paper.sqlite3")
    maker = PaperMarketMaker(Settings(maker_max_markets=1), store)
    store.adjust_inventory("yes", "condition", "Test market?", "YES", "10")
    store.adjust_inventory("no", "condition", "Test market?", "NO", "5")
    store.add_directional_buy("yes", "5", "2")
    store.set_paper_account("293", "300")

    result = maker.run([book()])

    assert result["maker_balanced_redeemed"] == 1
    assert store.paper_summary()["cash"] == "298"
    assert store.directional()["yes"]["shares"] == "5"
    inventory = {item["token_id"]: item["shares"] for item in store.inventory()}
    assert inventory == {"yes": "5"}
    store.close()


def test_new_market_must_end_inside_configured_window(tmp_path):
    store = AuditStore(tmp_path / "paper.sqlite3")
    maker = PaperMarketMaker(Settings(maker_max_markets=1), store)
    too_long = Market(
        **{
            **book().__dict__,
            "end_time": datetime.now(UTC) + timedelta(days=365),
        }
    )

    result = maker.run([too_long])

    assert result["maker_markets"] == 0
    assert result["maker_quotes"] == 0
    assert result["maker_rejected_horizon"] == 1
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
    dashboard = store.dashboard_state()
    assert Decimal(dashboard["performance"]["paired_pnl"]) > 0
    assert dashboard["trade_history"][0]["action"] == "PAIR MERGED"
    assert Decimal(dashboard["trade_history"][0]["realized_profit"]) > 0
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
    assert store.paper_summary()["fills"] == 1
    store.close()


def test_old_one_leg_is_force_flattened_instead_of_held_to_resolution(tmp_path):
    store = AuditStore(tmp_path / "paper.sqlite3")
    settings = Settings(
        maker_max_markets=1,
        maker_hedge_timeout_seconds=10,
        maker_force_flatten_seconds=20,
        maker_max_fee_rate=Decimal(0),
        maker_max_flatten_loss_per_share=Decimal(".001"),
    )
    maker = PaperMarketMaker(settings, store)
    store.adjust_inventory("yes", "condition", "Test market?", "YES", "5")
    store.add_directional_buy("yes", "5", "2.50")
    store.db.execute(
        "UPDATE paper_directional SET opened_at = ?",
        ((datetime.now(UTC) - timedelta(minutes=1)).isoformat(),),
    )
    store.db.commit()

    result = maker.run(
        [book(yes_bid=".30", yes_ask=".34", no_bid=".66", no_ask=".70")]
    )

    assert result["maker_hedge_exits"] == 1
    assert store.directional() == {}
    assert store.paper_summary()["fills"] == 1
    event = store.db.execute(
        "SELECT payload FROM events WHERE kind = 'paper_hedge_flattened'"
    ).fetchone()
    assert '"forced": true' in event[0]
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
    assert result["maker_rejected_toxic"] == 1
    store.close()


def test_complementary_yes_no_prices_are_not_misread_as_a_price_jump(tmp_path):
    store = AuditStore(tmp_path / "paper.sqlite3")
    maker = PaperMarketMaker(
        Settings(maker_max_markets=1, maker_max_midpoint_jump=Decimal(".03")),
        store,
    )
    asymmetric = book(
        yes_bid=".20",
        yes_ask=".24",
        no_bid=".76",
        no_ask=".80",
    )

    result = maker.run([asymmetric])

    assert result["maker_markets"] == 1
    assert result["maker_quotes"] == 2
    store.close()


def test_stale_two_sided_book_places_no_new_quotes(tmp_path):
    stale = datetime.now(UTC) - timedelta(minutes=2)
    stale_book = Market(
        **{
            **book().__dict__,
            "yes_updated_at": stale,
            "no_updated_at": stale,
        }
    )
    store = AuditStore(tmp_path / "paper.sqlite3")
    maker = PaperMarketMaker(
        Settings(maker_max_markets=1, maker_max_book_age_seconds=30),
        store,
    )

    result = maker.run([stale_book])

    assert result["maker_markets"] == 0
    assert result["maker_quotes"] == 0
    assert result["maker_rejected_stale"] == 1
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
    assert Decimal(dashboard["performance"]["directional_pnl"]) > 0
    assert dashboard["fills"][0]["side"] == "SELL"
    store.close()


def test_dashboard_keeps_last_quote_target_during_requote_gap(tmp_path):
    store = AuditStore(tmp_path / "paper.sqlite3")
    PaperMarketMaker(Settings(maker_max_markets=1), store)
    store.record(
        "realtime_cycle",
        {
            "maker_markets": 1,
            "maker_quotes": 3,
        },
    )

    dashboard = store.dashboard_state()

    assert dashboard["quotes"] == []
    assert dashboard["quote_target_count"] == 3
    store.close()
