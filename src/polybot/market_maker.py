from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, ROUND_UP, Decimal

from .config import Settings
from .models import Market
from .store import AuditStore


class PaperMarketMaker:
    """Conservative cross-snapshot simulator for post-only maker quotes."""

    def __init__(self, settings: Settings, store: AuditStore):
        self.s = settings
        self.store = store
        self.store.init_paper_account(str(settings.initial_equity))

    def run(self, markets: list[Market]) -> dict[str, int | str | bool]:
        token_books = self._token_books(markets)
        fills = self._settle_quotes(token_books)
        profit_exits = self._take_profitable_exits(token_books)
        selected = self._select_markets(markets)
        seeded = self._seed_complete_sets(selected)
        equity = self._equity(token_books)
        account = self.store.paper_account()
        peak = max(Decimal(account["peak_equity"]), equity)
        self.store.set_paper_account(account["cash"], str(peak))
        halted = equity <= Decimal(account["initial_equity"]) * (
            Decimal(1) - self.s.daily_loss_limit_pct
        )
        quotes = 0 if halted else self._place_quotes(selected)
        return {
            "maker_markets": len(selected),
            "maker_seeded": seeded,
            "maker_fills": fills,
            "maker_profit_exits": profit_exits,
            "maker_quotes": quotes,
            "paper_equity": f"{equity:.4f}",
            "maker_halted": halted,
        }

    def _select_markets(self, markets: list[Market]) -> list[Market]:
        now = datetime.now(UTC)
        existing = {item["condition_id"] for item in self.store.inventory()}
        existing_markets = [market for market in markets if market.condition_id in existing]
        existing_markets.sort(key=lambda market: market.liquidity, reverse=True)
        available_new_slots = max(0, self.s.maker_max_markets - len(existing))
        eligible = []
        for market in markets:
            if market.condition_id in existing:
                continue
            if market.end_time and market.end_time <= now + timedelta(
                hours=self.s.maker_min_hours_to_end
            ):
                continue
            prices = (market.yes_bid, market.yes_ask, market.no_bid, market.no_ask)
            if min(prices) < self.s.maker_min_price or max(prices) > self.s.maker_max_price:
                continue
            yes_spread = market.yes_ask - market.yes_bid
            no_spread = market.no_ask - market.no_bid
            if min(yes_spread, no_spread) < self.s.maker_min_spread:
                continue
            eligible.append((market, min(yes_spread, no_spread)))
        eligible.sort(key=lambda item: (item[1], item[0].liquidity), reverse=True)
        new_markets = [item[0] for item in eligible[:available_new_slots]]
        return existing_markets[: self.s.maker_max_markets] + new_markets

    def _seed_complete_sets(self, markets: list[Market]) -> int:
        account = self.store.paper_account()
        cash = Decimal(account["cash"])
        existing = {item["condition_id"] for item in self.store.inventory()}
        total_seeded = self.s.initial_equity - cash
        seeded = 0
        for market in markets:
            if market.condition_id in existing:
                continue
            shares = max(self.s.maker_order_shares, market.min_order_size)
            if (
                cash < shares
                or total_seeded + shares > self.s.maker_max_capital
            ):
                continue
            self.store.adjust_inventory(
                market.yes_token, market.condition_id, market.question, "YES", str(shares)
            )
            self.store.adjust_inventory(
                market.no_token, market.condition_id, market.question, "NO", str(shares)
            )
            cash -= shares
            total_seeded += shares
            seeded += 1
        self.store.set_paper_account(str(cash), account["peak_equity"])
        return seeded

    def _settle_quotes(self, books: dict[str, dict[str, Decimal | str]]) -> int:
        account = self.store.paper_account()
        cash = Decimal(account["cash"])
        directional = self.store.directional()
        fills = 0
        for quote in self.store.open_quotes():
            book = books.get(str(quote["token_id"]))
            price = Decimal(str(quote["price"]))
            size = Decimal(str(quote["size"]))
            fill = False
            if book and quote["side"] == "BUY":
                position = directional.get(str(quote["token_id"]))
                directional_shares = Decimal(position["shares"]) if position else Decimal(0)
                fill = (
                    Decimal(str(book["ask"])) < price
                    and cash >= price * size
                    and directional_shares + size <= self.s.maker_max_directional_shares
                )
            elif book and quote["side"] == "SELL":
                position = directional.get(str(quote["token_id"]))
                fill = (
                    Decimal(str(book["bid"])) > price
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
                else:
                    sold_cost = Decimal(
                        self.store.consume_directional(str(quote["token_id"]), str(size))
                    )
                    self.store.add_paper_metrics(str(price * size - sold_cost), "0")
                fills += 1
            else:
                self.store.close_quote(int(quote["id"]), "CANCELLED")
        self.store.set_paper_account(str(cash), account["peak_equity"])
        return fills

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
                    "shares": shares,
                    "bid": bid,
                    "average_cost": average_cost,
                    "fee": fee,
                    "slippage": slippage * shares,
                    "realized_profit": proceeds - sold_cost,
                },
            )
            exits += 1
        self.store.set_paper_account(str(cash), account["peak_equity"])
        return exits

    def _place_quotes(self, markets: list[Market]) -> int:
        directional = self.store.directional()
        account = self.store.paper_account()
        cash = Decimal(account["cash"])
        quotes = 0
        for market in markets:
            size = max(self.s.maker_order_shares, market.min_order_size)
            for outcome, token, bid, ask in (
                ("YES", market.yes_token, market.yes_bid, market.yes_ask),
                ("NO", market.no_token, market.no_bid, market.no_ask),
            ):
                tick = market.tick_size
                buy = min(bid + tick, ask - tick)
                sell = max(ask - tick, bid + tick)
                if buy < sell:
                    position = directional.get(token)
                    directional_shares = Decimal(position["shares"]) if position else Decimal(0)
                    if (
                        directional_shares + size <= self.s.maker_max_directional_shares
                        and cash >= buy * size
                    ):
                        self.store.add_quote(
                            token, market.condition_id, outcome, "BUY", str(buy), str(size)
                        )
                        quotes += 1
                    if position and Decimal(position["shares"]) >= size:
                        average_cost = Decimal(position["cost_basis"]) / Decimal(
                            position["shares"]
                        )
                        sell = max(sell, average_cost + self.s.maker_take_profit_per_share)
                        sell = (sell / tick).to_integral_value(rounding=ROUND_UP) * tick
                        if sell >= Decimal(1):
                            continue
                        self.store.add_quote(
                            token,
                            market.condition_id,
                            outcome,
                            "SELL",
                            str(sell),
                            str(size),
                        )
                        quotes += 1
        return quotes

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
            }
            result[market.no_token] = {
                "bid": market.no_bid,
                "ask": market.no_ask,
                "question": market.question,
            }
        return result
