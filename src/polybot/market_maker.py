from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal

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
        inventory = {
            item["token_id"]: Decimal(item["shares"]) for item in self.store.inventory()
        }
        fills = 0
        for quote in self.store.open_quotes():
            book = books.get(str(quote["token_id"]))
            price = Decimal(str(quote["price"]))
            size = Decimal(str(quote["size"]))
            fill = False
            if book and quote["side"] == "BUY":
                fill = Decimal(str(book["ask"])) < price and cash >= price * size
            elif book and quote["side"] == "SELL":
                fill = (
                    Decimal(str(book["bid"])) > price
                    and inventory.get(str(quote["token_id"]), Decimal(0)) >= size
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
                fills += 1
            else:
                self.store.close_quote(int(quote["id"]), "CANCELLED")
        self.store.set_paper_account(str(cash), account["peak_equity"])
        return fills

    def _place_quotes(self, markets: list[Market]) -> int:
        inventory = {
            item["token_id"]: Decimal(item["shares"]) for item in self.store.inventory()
        }
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
                    self.store.add_quote(
                        token, market.condition_id, outcome, "BUY", str(buy), str(size)
                    )
                    quotes += 1
                    if inventory.get(token, Decimal(0)) >= size:
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
