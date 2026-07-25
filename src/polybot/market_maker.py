from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, ROUND_UP, Decimal

from .config import Settings
from .models import Market
from .store import AuditStore


class PaperMarketMaker:
    """Pair-safe, inventory-aware simulator for post-only maker quotes."""

    def __init__(self, settings: Settings, store: AuditStore):
        self.s = settings
        self.store = store
        self._last_selection_stats: dict[str, int] = {}
        self.store.init_paper_account(str(settings.initial_equity))

    def run(self, markets: list[Market]) -> dict[str, int | str | bool]:
        released = self._release_balanced_inventory()
        self._record_snapshots(markets)
        token_books = self._token_books(markets)
        fills = self._settle_quotes(token_books)
        merged = self._merge_directional_pairs(markets)
        profit_exits = self._take_profitable_exits(token_books)
        hedge_exits = self._resolve_expired_hedges(markets, token_books)
        merged += self._merge_directional_pairs(markets)
        selected = self._select_markets(markets)
        equity = self._equity(token_books)
        account = self.store.paper_account()
        peak = max(Decimal(account["peak_equity"]), equity)
        self.store.set_paper_account(account["cash"], str(peak))
        halted = equity <= Decimal(account["initial_equity"]) * (
            Decimal(1) - self.s.daily_loss_limit_pct
        )
        quotes = self._place_quotes([] if halted else selected)
        return {
            "maker_markets": len(selected),
            "maker_scanned_markets": self._last_selection_stats.get("scanned", 0),
            "maker_rejected_stale": self._last_selection_stats.get("stale", 0),
            "maker_rejected_horizon": self._last_selection_stats.get("horizon", 0),
            "maker_rejected_price": self._last_selection_stats.get("price", 0),
            "maker_rejected_spread": self._last_selection_stats.get("spread", 0),
            "maker_rejected_toxic": self._last_selection_stats.get("toxic", 0),
            "maker_seeded": 0,
            "maker_balanced_redeemed": released,
            "maker_fills": fills,
            "maker_pairs_merged": merged,
            "maker_profit_exits": profit_exits,
            "maker_hedge_exits": hedge_exits,
            "maker_quotes": quotes,
            "maker_reward_markets": sum(
                1 for market in selected if self._reward_eligible(market)
            ),
            "paper_equity": f"{equity:.4f}",
            "maker_halted": halted,
        }

    def selected_markets(self, markets: list[Market]) -> list[Market]:
        """Expose deterministic selection for the real-time subscription set."""
        return self._select_markets(markets)

    def _select_markets(self, markets: list[Market]) -> list[Market]:
        now = datetime.now(UTC)
        stats = {
            "scanned": len(markets),
            "stale": 0,
            "horizon": 0,
            "price": 0,
            "spread": 0,
            "toxic": 0,
        }
        existing = {item["condition_id"] for item in self.store.inventory()}
        existing_markets = []
        for market in markets:
            if market.condition_id not in existing:
                continue
            if self._market_book_is_stale(market, now):
                stats["stale"] += 1
                continue
            if self._is_toxic(market):
                stats["toxic"] += 1
                continue
            existing_markets.append(market)
        existing_markets.sort(key=lambda market: market.liquidity, reverse=True)
        available_new_slots = max(0, self.s.maker_max_markets - len(existing))
        eligible: list[tuple[Market, Decimal]] = []
        for market in markets:
            if market.condition_id in existing:
                continue
            if self._market_book_is_stale(market, now):
                stats["stale"] += 1
                continue
            if not market.end_time:
                stats["horizon"] += 1
                continue
            time_to_end = market.end_time - now
            if time_to_end <= timedelta(hours=self.s.maker_min_hours_to_end):
                stats["horizon"] += 1
                continue
            if time_to_end > timedelta(hours=self.s.maker_max_hours_to_end):
                stats["horizon"] += 1
                continue
            prices = (market.yes_bid, market.yes_ask, market.no_bid, market.no_ask)
            if min(prices) < self.s.maker_min_price or max(prices) > self.s.maker_max_price:
                stats["price"] += 1
                continue
            yes_spread = market.yes_ask - market.yes_bid
            no_spread = market.no_ask - market.no_bid
            if min(yes_spread, no_spread) < self.s.maker_min_spread:
                stats["spread"] += 1
                continue
            if self._is_toxic(market):
                stats["toxic"] += 1
                continue
            eligible.append((market, self._selection_score(market)))
        eligible.sort(key=lambda item: (item[1], item[0].liquidity), reverse=True)
        new_markets = [item[0] for item in eligible[:available_new_slots]]
        self._last_selection_stats = stats
        return existing_markets[: self.s.maker_max_markets] + new_markets

    def _release_balanced_inventory(self) -> int:
        """Return non-directional YES/NO pairs to paper cash immediately.

        Older releases seeded complete sets merely to initialize inventory. Those
        pairs have no directional exposure and can be merged for $1 without
        waiting for market resolution. Directional cost-basis shares are kept.
        """
        account = self.store.paper_account()
        cash = Decimal(account["cash"])
        inventory = self.store.inventory()
        directional = self.store.directional()
        by_condition: dict[str, dict[str, dict[str, str]]] = {}
        for item in inventory:
            by_condition.setdefault(item["condition_id"], {})[item["outcome"]] = item
        released = 0
        for condition_id, outcomes in by_condition.items():
            yes = outcomes.get("YES")
            no = outcomes.get("NO")
            if not yes or not no:
                continue
            yes_directional = Decimal(
                directional.get(yes["token_id"], {}).get("shares", "0")
            )
            no_directional = Decimal(
                directional.get(no["token_id"], {}).get("shares", "0")
            )
            shares = min(
                max(Decimal(0), Decimal(yes["shares"]) - yes_directional),
                max(Decimal(0), Decimal(no["shares"]) - no_directional),
            )
            if shares <= 0:
                continue
            self.store.adjust_inventory(
                yes["token_id"],
                condition_id,
                yes["question"],
                "YES",
                str(-shares),
            )
            self.store.adjust_inventory(
                no["token_id"],
                condition_id,
                no["question"],
                "NO",
                str(-shares),
            )
            cash += shares
            self.store.record(
                "paper_balanced_redeemed",
                {
                    "condition_id": condition_id,
                    "question": yes["question"],
                    "shares": shares,
                    "payout": shares,
                    "reason": "legacy_seed_release",
                },
            )
            released += 1
        self.store.set_paper_account(str(cash), account["peak_equity"])
        return released

    def _settle_quotes(self, books: dict[str, dict[str, Decimal | str]]) -> int:
        account = self.store.paper_account()
        cash = Decimal(account["cash"])
        directional = self.store.directional()
        equity = self._equity(books)
        capital_cap = self._maker_capital_cap(equity)
        deployed = self._maker_deployed_capital()
        fills = 0
        for quote in self.store.open_quotes():
            book = books.get(str(quote["token_id"]))
            price = Decimal(str(quote["price"]))
            size = Decimal(str(quote["size"]))
            fill = False
            fill_evidence = ""
            observed_volume = Decimal(0)
            if not book:
                self.store.close_quote(int(quote["id"]), "NO_BOOK")
                continue
            if self._book_is_stale(book):
                self.store.close_quote(int(quote["id"]), "STALE")
                continue
            if quote["side"] == "BUY":
                position = directional.get(str(quote["token_id"]))
                crossed = Decimal(str(book["ask"])) < price
                observed_volume = Decimal(
                    self.store.public_trade_volume(
                        str(quote["token_id"]),
                        "SELL",
                        str(price),
                        str(quote["created_at"]),
                        at_or_better="below",
                    )
                )
                queue_consumed = observed_volume >= (
                    Decimal(str(quote["queue_ahead"])) + size
                )
                fill = crossed or queue_consumed
                fill_evidence = "book_cross" if crossed else "public_trade"
                fill = (
                    fill
                    and cash >= price * size
                    and self._directional_room(position, price, size)
                    and deployed + price * size <= capital_cap
                )
            elif quote["side"] == "SELL":
                position = directional.get(str(quote["token_id"]))
                crossed = Decimal(str(book["bid"])) > price
                observed_volume = Decimal(
                    self.store.public_trade_volume(
                        str(quote["token_id"]),
                        "BUY",
                        str(price),
                        str(quote["created_at"]),
                        at_or_better="above",
                    )
                )
                queue_consumed = observed_volume >= (
                    Decimal(str(quote["queue_ahead"])) + size
                )
                fill = crossed or queue_consumed
                fill_evidence = "book_cross" if crossed else "public_trade"
                fill = (
                    fill
                    and position is not None
                    and Decimal(position["shares"]) >= size
                )
            if fill:
                delta = size if quote["side"] == "BUY" else -size
                cash += -price * size if quote["side"] == "BUY" else price * size
                self.store.adjust_inventory(
                    str(quote["token_id"]),
                    str(quote["condition_id"]),
                    str(book["question"]),
                    str(quote["outcome"]),
                    str(delta),
                )
                self.store.add_fill(
                    int(quote["id"]),
                    str(quote["token_id"]),
                    str(quote["side"]),
                    str(price),
                    str(size),
                )
                if quote["side"] == "BUY":
                    self.store.add_directional_buy(
                        str(quote["token_id"]), str(size), str(price * size)
                    )
                    deployed += price * size
                else:
                    sold_cost = Decimal(
                        self.store.consume_directional(str(quote["token_id"]), str(size))
                    )
                    self.store.add_paper_metrics(str(price * size - sold_cost), "0")
                self.store.record(
                    "paper_quote_filled",
                    {
                        "quote_id": quote["id"],
                        "token_id": quote["token_id"],
                        "side": quote["side"],
                        "price": price,
                        "shares": size,
                        "evidence": fill_evidence,
                        "queue_ahead": quote["queue_ahead"],
                        "observed_trade_volume": observed_volume,
                    },
                )
                fills += 1
        self.store.set_paper_account(str(cash), account["peak_equity"])
        return fills

    def _merge_directional_pairs(self, markets: list[Market]) -> int:
        account = self.store.paper_account()
        cash = Decimal(account["cash"])
        directional = self.store.directional()
        merged = 0
        for market in markets:
            yes = directional.get(market.yes_token)
            no = directional.get(market.no_token)
            if not yes or not no:
                continue
            shares = min(Decimal(yes["shares"]), Decimal(no["shares"]))
            if shares <= 0:
                continue
            yes_cost = Decimal(
                self.store.consume_directional(market.yes_token, str(shares))
            )
            no_cost = Decimal(
                self.store.consume_directional(market.no_token, str(shares))
            )
            self.store.adjust_inventory(
                market.yes_token,
                market.condition_id,
                market.question,
                "YES",
                str(-shares),
            )
            self.store.adjust_inventory(
                market.no_token,
                market.condition_id,
                market.question,
                "NO",
                str(-shares),
            )
            cash += shares
            profit = shares - yes_cost - no_cost
            opened_at = min(
                datetime.fromisoformat(str(yes["opened_at"])),
                datetime.fromisoformat(str(no["opened_at"])),
            )
            self.store.add_paper_metrics(str(profit), "0")
            self.store.record(
                "paper_pair_merged",
                {
                    "condition_id": market.condition_id,
                    "question": market.question,
                    "outcome": "YES + NO",
                    "shares": shares,
                    "combined_cost": yes_cost + no_cost,
                    "payout": shares,
                    "realized_profit": profit,
                    "opened_at": opened_at.isoformat(),
                    "held_seconds": (
                        datetime.now(UTC) - opened_at
                    ).total_seconds(),
                },
            )
            merged += 1
        self.store.set_paper_account(str(cash), account["peak_equity"])
        return merged

    def _take_profitable_exits(
        self, books: dict[str, dict[str, Decimal | str]]
    ) -> int:
        account = self.store.paper_account()
        cash = Decimal(account["cash"])
        exits = 0
        slippage = self.s.slippage_bps / Decimal(10000)
        inventory = {
            item["token_id"]: item for item in self.store.inventory()
        }
        for token_id, position in self.store.directional().items():
            book = books.get(token_id)
            item = inventory.get(token_id)
            if not book or not item:
                continue
            if self._book_is_stale(book):
                continue
            shares = Decimal(position["shares"])
            if shares <= 0:
                continue
            bid = Decimal(str(book["bid"]))
            average_cost = Decimal(position["cost_basis"]) / shares
            fee_per_share = self.s.maker_max_fee_rate * bid * (Decimal(1) - bid)
            net_price = bid - fee_per_share - slippage
            if net_price < average_cost + self.s.maker_take_profit_per_share:
                continue
            fee = fee_per_share * shares
            proceeds = bid * shares - fee - slippage * shares
            sold_cost = Decimal(self.store.consume_directional(token_id, str(shares)))
            self.store.adjust_inventory(
                token_id,
                item["condition_id"],
                item["question"],
                item["outcome"],
                str(-shares),
            )
            cash += proceeds
            self.store.add_paper_metrics(str(proceeds - sold_cost), str(fee))
            quote_id = self.store.add_quote(
                token_id,
                item["condition_id"],
                item["outcome"],
                "SELL",
                str(bid),
                str(shares),
            )
            self.store.add_fill(quote_id, token_id, "SELL", str(bid), str(shares))
            self.store.record(
                "paper_profit_exit",
                {
                    "token_id": token_id,
                    "condition_id": item["condition_id"],
                    "question": item["question"],
                    "outcome": item["outcome"],
                    "shares": shares,
                    "bid": bid,
                    "average_cost": average_cost,
                    "fee": fee,
                    "slippage": slippage * shares,
                    "realized_profit": proceeds - sold_cost,
                    "opened_at": position["opened_at"],
                    "held_seconds": (
                        datetime.now(UTC)
                        - datetime.fromisoformat(str(position["opened_at"]))
                    ).total_seconds(),
                },
            )
            exits += 1
        self.store.set_paper_account(str(cash), account["peak_equity"])
        return exits

    def manual_close(self, token_id: str, requested_shares: Decimal | None = None) -> dict[str, str]:
        """Close a directional paper position at the latest recorded bid."""
        directional = self.store.directional().get(token_id)
        inventory = {
            item["token_id"]: item for item in self.store.inventory()
        }.get(token_id)
        book = self.store.latest_book(token_id)
        if not directional or not inventory:
            raise ValueError("No directional paper position exists for this token.")
        if not book:
            raise ValueError("No current executable book is available for this token.")
        available = Decimal(directional["shares"])
        shares = available if requested_shares is None else requested_shares
        if shares <= 0 or shares > available:
            raise ValueError("Close size must be positive and cannot exceed the position.")
        bid = Decimal(book["bid"])
        slippage = self.s.slippage_bps / Decimal(10000)
        fee_per_share = self.s.maker_max_fee_rate * bid * (Decimal(1) - bid)
        net_price = bid - fee_per_share - slippage
        proceeds = net_price * shares
        account = self.store.paper_account()
        sold_cost = Decimal(self.store.consume_directional(token_id, str(shares)))
        self.store.adjust_inventory(
            token_id,
            inventory["condition_id"],
            inventory["question"],
            inventory["outcome"],
            str(-shares),
        )
        self.store.set_paper_account(
            str(Decimal(account["cash"]) + proceeds), account["peak_equity"]
        )
        fee = fee_per_share * shares
        profit = proceeds - sold_cost
        self.store.add_paper_metrics(str(profit), str(fee))
        quote_id = self.store.add_quote(
            token_id,
            inventory["condition_id"],
            inventory["outcome"],
            "SELL",
            str(bid),
            str(shares),
        )
        self.store.add_fill(quote_id, token_id, "SELL", str(bid), str(shares))
        result = {
            "token_id": token_id,
            "condition_id": inventory["condition_id"],
            "question": inventory["question"],
            "outcome": inventory["outcome"],
            "shares": str(shares),
            "bid": str(bid),
            "net_price": str(net_price),
            "fee": str(fee),
            "realized_profit": str(profit),
            "opened_at": directional["opened_at"],
            "held_seconds": str(
                (
                    datetime.now(UTC)
                    - datetime.fromisoformat(str(directional["opened_at"]))
                ).total_seconds()
            ),
        }
        self.store.record("paper_manual_close", result)
        return result

    def _resolve_expired_hedges(
        self,
        markets: list[Market],
        books: dict[str, dict[str, Decimal | str]],
    ) -> int:
        """Cap one-leg risk after the hedge timer using the cheapest safe exit."""
        account = self.store.paper_account()
        cash = Decimal(account["cash"])
        now = datetime.now(UTC)
        slippage = self.s.slippage_bps / Decimal(10000)
        by_token = {
            market.yes_token: (market, "YES", market.no_token, market.no_ask)
            for market in markets
        }
        by_token.update(
            {
                market.no_token: (market, "NO", market.yes_token, market.yes_ask)
                for market in markets
            }
        )
        resolved = 0
        for token_id, position in list(self.store.directional().items()):
            details = by_token.get(token_id)
            book = books.get(token_id)
            if not details or not book:
                continue
            if self._book_is_stale(book):
                self.store.record(
                    "paper_hedge_stale_book",
                    {
                        "token_id": token_id,
                        "condition_id": details[0].condition_id,
                    },
                )
                continue
            opened_at = datetime.fromisoformat(position["opened_at"])
            age_seconds = (now - opened_at).total_seconds()
            if age_seconds < self.s.maker_hedge_timeout_seconds:
                continue
            market, outcome, opposite_token, opposite_ask = details
            shares = Decimal(position["shares"])
            average_cost = Decimal(position["cost_basis"]) / shares
            opposite_fee = (
                self.s.maker_max_fee_rate
                * opposite_ask
                * (Decimal(1) - opposite_ask)
            )
            hedge_unit_cost = opposite_ask + opposite_fee + slippage
            pair_profit = Decimal(1) - average_cost - hedge_unit_cost
            hedge_notional = hedge_unit_cost * shares
            if (
                pair_profit >= -self.s.maker_max_flatten_loss_per_share
                and cash >= hedge_notional
            ):
                opposite_outcome = "NO" if outcome == "YES" else "YES"
                self.store.adjust_inventory(
                    opposite_token,
                    market.condition_id,
                    market.question,
                    opposite_outcome,
                    str(shares),
                )
                self.store.add_directional_buy(
                    opposite_token, str(shares), str(hedge_notional)
                )
                quote_id = self.store.add_quote(
                    opposite_token,
                    market.condition_id,
                    opposite_outcome,
                    "BUY",
                    str(opposite_ask),
                    str(shares),
                )
                self.store.add_fill(
                    quote_id,
                    opposite_token,
                    "BUY",
                    str(opposite_ask),
                    str(shares),
                )
                cash -= hedge_notional
                self.store.add_paper_metrics("0", str(opposite_fee * shares))
                self.store.record(
                    "paper_hedge_escalated",
                    {
                        "condition_id": market.condition_id,
                        "question": market.question,
                        "held_outcome": outcome,
                        "hedge_outcome": opposite_outcome,
                        "shares": shares,
                        "opposite_ask": opposite_ask,
                        "projected_pair_profit_per_share": pair_profit,
                        "opened_at": position["opened_at"],
                        "held_seconds": age_seconds,
                    },
                )
                resolved += 1
                continue
            bid = Decimal(str(book["bid"]))
            fee_per_share = self.s.maker_max_fee_rate * bid * (Decimal(1) - bid)
            net_price = bid - fee_per_share - slippage
            loss_per_share = average_cost - net_price
            force_flatten = age_seconds >= self.s.maker_force_flatten_seconds
            if (
                loss_per_share > self.s.maker_max_flatten_loss_per_share
                and not force_flatten
            ):
                self.store.record(
                    "paper_hedge_blocked",
                    {
                        "condition_id": market.condition_id,
                        "outcome": outcome,
                        "loss_per_share": loss_per_share,
                        "loss_cap": self.s.maker_max_flatten_loss_per_share,
                        "force_flatten_in_seconds": (
                            self.s.maker_force_flatten_seconds - age_seconds
                        ),
                    },
                )
                continue
            proceeds = net_price * shares
            sold_cost = Decimal(self.store.consume_directional(token_id, str(shares)))
            self.store.adjust_inventory(
                token_id,
                market.condition_id,
                market.question,
                outcome,
                str(-shares),
            )
            cash += proceeds
            self.store.add_paper_metrics(
                str(proceeds - sold_cost), str(fee_per_share * shares)
            )
            quote_id = self.store.add_quote(
                token_id,
                market.condition_id,
                outcome,
                "SELL",
                str(bid),
                str(shares),
            )
            self.store.add_fill(
                quote_id,
                token_id,
                "SELL",
                str(bid),
                str(shares),
            )
            self.store.record(
                "paper_hedge_flattened",
                {
                    "condition_id": market.condition_id,
                    "question": market.question,
                    "outcome": outcome,
                    "shares": shares,
                    "net_price": net_price,
                    "realized_profit": proceeds - sold_cost,
                    "forced": force_flatten,
                    "opened_at": position["opened_at"],
                    "held_seconds": age_seconds,
                },
            )
            resolved += 1
        self.store.set_paper_account(str(cash), account["peak_equity"])
        return resolved

    def _place_quotes(self, markets: list[Market]) -> int:
        directional = self.store.directional()
        account = self.store.paper_account()
        cash = Decimal(account["cash"])
        equity = self._equity(self._token_books(markets))
        capital_cap = self._maker_capital_cap(equity)
        deployed = self._maker_deployed_capital()
        committed = Decimal(0)
        desired: list[dict[str, str]] = []
        for market in markets:
            size = self._order_size(market, equity)
            if size <= 0:
                continue
            yes_buy, no_buy = self._pair_buy_prices(market, directional)
            planned = (
                (
                    "YES",
                    market.yes_token,
                    yes_buy,
                    market.yes_bid,
                    market.yes_bid_size,
                    market.yes_ask,
                    market.yes_ask_size,
                ),
                (
                    "NO",
                    market.no_token,
                    no_buy,
                    market.no_bid,
                    market.no_bid_size,
                    market.no_ask,
                    market.no_ask_size,
                ),
            )
            for outcome, token, buy, bid, bid_size, ask, ask_size in planned:
                if buy is None:
                    continue
                position = directional.get(token)
                notional = buy * size
                if (
                    self._directional_room(position, buy, size, equity)
                    and cash - committed >= notional
                    and deployed + committed + notional <= capital_cap
                ):
                    desired.append(
                        {
                            "token_id": token,
                            "condition_id": market.condition_id,
                            "question": market.question,
                            "outcome": outcome,
                            "side": "BUY",
                            "price": str(buy),
                            "size": str(size),
                            "queue_ahead": str(bid_size if buy <= bid else Decimal(0)),
                        }
                    )
                    committed += notional
                if not position or Decimal(position["shares"]) < size:
                    continue
                average_cost = Decimal(position["cost_basis"]) / Decimal(
                    position["shares"]
                )
                sell = max(
                    ask - market.tick_size,
                    bid + market.tick_size,
                    average_cost + self.s.maker_take_profit_per_share,
                )
                sell = (
                    sell / market.tick_size
                ).to_integral_value(rounding=ROUND_UP) * market.tick_size
                if sell < Decimal(1):
                    desired.append(
                        {
                            "token_id": token,
                            "condition_id": market.condition_id,
                            "question": market.question,
                            "outcome": outcome,
                            "side": "SELL",
                            "price": str(sell),
                            "size": str(size),
                            "queue_ahead": str(
                                ask_size if sell >= ask else Decimal(0)
                            ),
                        }
                    )
        desired_by_key = {self._quote_key(quote): quote for quote in desired}
        retained: set[tuple[str, str, Decimal, Decimal]] = set()
        for quote in self.store.open_quotes():
            key = self._quote_key(quote)
            if key in desired_by_key and key not in retained:
                retained.add(key)
            else:
                self.store.close_quote(int(quote["id"]), "CANCELLED")
        for key, quote in desired_by_key.items():
            if key in retained:
                continue
            self.store.add_quote(
                quote["token_id"],
                quote["condition_id"],
                quote["outcome"],
                quote["side"],
                quote["price"],
                quote["size"],
                quote["queue_ahead"],
                quote["question"],
            )
        return len(desired_by_key)

    @staticmethod
    def _quote_key(
        quote: dict[str, str | int],
    ) -> tuple[str, str, Decimal, Decimal]:
        return (
            str(quote["token_id"]),
            str(quote["side"]),
            Decimal(str(quote["price"])),
            Decimal(str(quote["size"])),
        )

    def _pair_buy_prices(
        self,
        market: Market,
        directional: dict[str, dict[str, str]],
    ) -> tuple[Decimal | None, Decimal | None]:
        tick = market.tick_size
        yes_position = directional.get(market.yes_token)
        no_position = directional.get(market.no_token)
        yes_shares = Decimal(yes_position["shares"]) if yes_position else Decimal(0)
        no_shares = Decimal(no_position["shares"]) if no_position else Decimal(0)
        imbalance = yes_shares - no_shares
        fair_yes = self._fair_yes(market)
        skew = max(
            Decimal("-0.05"),
            min(
                Decimal("0.05"),
                imbalance * self.s.maker_inventory_skew_per_share,
            ),
        )
        reservation_yes = fair_yes - skew
        reservation_no = Decimal(1) - reservation_yes
        half_edge = self.s.maker_pair_min_edge / Decimal(2)
        yes_buy = min(
            market.yes_bid + tick,
            market.yes_ask - tick,
            reservation_yes - half_edge,
        )
        no_buy = min(
            market.no_bid + tick,
            market.no_ask - tick,
            reservation_no - half_edge,
        )
        if yes_position and not no_position:
            average = Decimal(yes_position["cost_basis"]) / yes_shares
            no_buy = min(no_buy, Decimal(1) - average - self.s.maker_pair_min_edge)
        elif no_position and not yes_position:
            average = Decimal(no_position["cost_basis"]) / no_shares
            yes_buy = min(yes_buy, Decimal(1) - average - self.s.maker_pair_min_edge)
        cap = Decimal(1) - self.s.maker_pair_min_edge
        excess = yes_buy + no_buy - cap
        if excess > 0:
            yes_buy -= excess / Decimal(2)
            no_buy -= excess - excess / Decimal(2)
        yes_buy = (yes_buy / tick).to_integral_value(rounding=ROUND_DOWN) * tick
        no_buy = (no_buy / tick).to_integral_value(rounding=ROUND_DOWN) * tick
        if yes_buy < market.yes_bid or yes_buy >= market.yes_ask:
            yes_buy = None
        if no_buy < market.no_bid or no_buy >= market.no_ask:
            no_buy = None
        return yes_buy, no_buy

    @staticmethod
    def _microprice(
        bid: Decimal,
        ask: Decimal,
        bid_size: Decimal,
        ask_size: Decimal,
    ) -> Decimal:
        total = bid_size + ask_size
        if total <= 0:
            return (bid + ask) / Decimal(2)
        return (ask * bid_size + bid * ask_size) / total

    def _fair_yes(self, market: Market) -> Decimal:
        yes = self._microprice(
            market.yes_bid,
            market.yes_ask,
            market.yes_bid_size,
            market.yes_ask_size,
        )
        no = self._microprice(
            market.no_bid,
            market.no_ask,
            market.no_bid_size,
            market.no_ask_size,
        )
        total = yes + no
        return yes / total if total > 0 else (market.yes_bid + market.yes_ask) / 2

    def _selection_score(self, market: Market) -> Decimal:
        spread = min(
            market.yes_ask - market.yes_bid,
            market.no_ask - market.no_bid,
        )
        reward = Decimal(0)
        if self._reward_eligible(market):
            reward = self.s.maker_reward_weight * (
                market.reward_daily_rate / max(Decimal(1), self.s.maker_max_capital)
                + Decimal("0.01")
            )
        return spread + reward

    def _reward_eligible(self, market: Market) -> bool:
        if market.reward_max_spread <= 0:
            return False
        size = self._order_size(market, self.s.initial_equity)
        return market.reward_min_size <= size

    def _order_size(self, market: Market, equity: Decimal) -> Decimal:
        required = max(self.s.maker_order_shares, market.min_order_size)
        if not self.s.maker_compound:
            return required
        target = (equity * self.s.maker_order_equity_pct).to_integral_value(
            rounding=ROUND_DOWN
        )
        market_cap = (equity * self.s.max_market_exposure_pct).to_integral_value(
            rounding=ROUND_DOWN
        )
        if market_cap < required:
            return Decimal(0)
        return min(max(required, target), market_cap)

    def _maker_capital_cap(self, equity: Decimal) -> Decimal:
        if self.s.maker_compound:
            return equity * self.s.maker_max_capital_pct
        return self.s.maker_max_capital

    def _maker_deployed_capital(self) -> Decimal:
        balanced = self._balanced_inventory_capital(self.store.inventory())
        directional = sum(
            (
                Decimal(position["cost_basis"])
                for position in self.store.directional().values()
            ),
            Decimal(0),
        )
        return balanced + directional

    def _directional_room(
        self,
        position: dict[str, str] | None,
        price: Decimal,
        size: Decimal,
        equity: Decimal | None = None,
    ) -> bool:
        if not self.s.maker_compound:
            shares = Decimal(position["shares"]) if position else Decimal(0)
            return shares + size <= self.s.maker_max_directional_shares
        if equity is None:
            state = self.store.dashboard_state()["account"]
            equity = Decimal(state["equity"])
        current_cost = Decimal(position["cost_basis"]) if position else Decimal(0)
        return (
            current_cost + price * size
            <= equity * self.s.maker_max_directional_exposure_pct
        )

    @staticmethod
    def _balanced_inventory_capital(inventory: list[dict[str, str]]) -> Decimal:
        by_condition: dict[str, list[Decimal]] = {}
        for item in inventory:
            by_condition.setdefault(item["condition_id"], []).append(
                Decimal(item["shares"])
            )
        return sum(
            (min(shares) for shares in by_condition.values() if len(shares) >= 2),
            Decimal(0),
        )

    def _market_book_is_stale(
        self, market: Market, now: datetime | None = None
    ) -> bool:
        now = now or datetime.now(UTC)
        oldest = min(market.yes_updated_at, market.no_updated_at)
        return (now - oldest).total_seconds() > self.s.maker_max_book_age_seconds

    def _book_is_stale(self, book: dict[str, Decimal | str | datetime]) -> bool:
        updated_at = book.get("updated_at")
        return (
            not isinstance(updated_at, datetime)
            or (datetime.now(UTC) - updated_at).total_seconds()
            > self.s.maker_max_book_age_seconds
        )

    def _record_snapshots(self, markets: list[Market]) -> None:
        for market in markets:
            for token_id, bid, ask in (
                (market.yes_token, market.yes_bid, market.yes_ask),
                (market.no_token, market.no_bid, market.no_ask),
            ):
                self.store.record_tick(
                    market.condition_id,
                    token_id,
                    str(bid),
                    str(ask),
                    "snapshot",
                )

    def _is_toxic(self, market: Market) -> bool:
        since = datetime.now(UTC) - timedelta(
            seconds=self.s.maker_toxicity_window_seconds
        )
        jump = max(
            Decimal(self.store.midpoint_jump(token_id, since.isoformat()))
            for token_id in (market.yes_token, market.no_token)
        )
        return jump >= self.s.maker_max_midpoint_jump

    def _equity(self, books: dict[str, dict[str, Decimal | str]]) -> Decimal:
        account = self.store.paper_account()
        value = Decimal(account["cash"])
        for item in self.store.inventory():
            book = books.get(item["token_id"])
            if book:
                midpoint = (Decimal(str(book["bid"])) + Decimal(str(book["ask"]))) / 2
                value += Decimal(item["shares"]) * midpoint
        return value.quantize(Decimal("0.0001"), rounding=ROUND_DOWN)

    @staticmethod
    def _token_books(markets: list[Market]) -> dict[str, dict[str, Decimal | str]]:
        result: dict[str, dict[str, Decimal | str]] = {}
        for market in markets:
            result[market.yes_token] = {
                "bid": market.yes_bid,
                "ask": market.yes_ask,
                "question": market.question,
                "updated_at": market.yes_updated_at,
            }
            result[market.no_token] = {
                "bid": market.no_bid,
                "ask": market.no_ask,
                "question": market.question,
                "updated_at": market.no_updated_at,
            }
        return result
