import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from .config import Settings
from .models import Market, MarketGroup


def _jsonish(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    return []


def _decimal(value: Any, default: str = "0") -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def _time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


class GammaClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.client = httpx.AsyncClient(
            base_url=settings.gamma_url, timeout=20, transport=transport
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def active_binary_markets(self, limit: int | None = None) -> list[Market]:
        target = limit or self.settings.discovery_limit
        raw_markets: list[dict[str, Any]] = []
        page_size = min(100, target)
        while len(raw_markets) < target:
            response = await self.client.get(
                "/markets",
                params={
                    "active": "true",
                    "closed": "false",
                    "limit": min(page_size, target - len(raw_markets)),
                    "offset": len(raw_markets),
                    "order": "liquidityNum",
                    "ascending": "false",
                },
            )
            response.raise_for_status()
            page = response.json()
            raw_markets.extend(page)
            if len(page) < page_size:
                break
        result: list[Market] = []
        for raw in raw_markets:
            market = self._parse_market(raw, require_liquidity=True)
            if market:
                result.append(market)
        return result

    async def active_negative_risk_groups(self) -> list[MarketGroup]:
        response = await self.client.get(
            "/events",
            params={
                "active": "true",
                "closed": "false",
                "limit": self.settings.neg_risk_event_limit,
                "order": "liquidity",
                "ascending": "false",
            },
        )
        response.raise_for_status()
        groups: list[MarketGroup] = []
        for event in response.json():
            raw_markets = event.get("markets") or []
            if not event.get("negRisk") or not 3 <= len(raw_markets) <= self.settings.neg_risk_max_outcomes:
                continue
            # Completeness is mandatory: if any event leg is inactive, closed,
            # malformed, or missing tokens, reject the whole event.
            if any(not item.get("active") or item.get("closed") for item in raw_markets):
                continue
            parsed = [self._parse_market(item, require_liquidity=False) for item in raw_markets]
            if any(item is None for item in parsed):
                continue
            groups.append(
                MarketGroup(
                    event_id=str(event.get("id", "")),
                    title=str(event.get("title", "")),
                    neg_risk_id=str(event.get("negRiskMarketID", "")),
                    markets=tuple(item for item in parsed if item),
                )
            )
        return groups

    def _parse_market(self, raw: dict[str, Any], require_liquidity: bool) -> Market | None:
        outcomes = [str(x).upper() for x in _jsonish(raw.get("outcomes"))]
        tokens = [str(x) for x in _jsonish(raw.get("clobTokenIds"))]
        prices = [_decimal(x) for x in _jsonish(raw.get("outcomePrices"))]
        if len(outcomes) != 2 or len(tokens) != 2 or len(prices) != 2:
            return None
        if "YES" not in outcomes or "NO" not in outcomes:
            if require_liquidity:
                yes_index, no_index = 0, 1
            else:
                return None
        else:
            yes_index, no_index = outcomes.index("YES"), outcomes.index("NO")
        liquidity = _decimal(raw.get("liquidityNum", raw.get("liquidity", 0)))
        if require_liquidity and liquidity < self.settings.min_liquidity:
            return None
        yes, no = prices[yes_index], prices[no_index]
        spread = Decimal("0.01")
        return Market(
            condition_id=str(raw.get("conditionId", "")),
            question=str(raw.get("question", "")),
            yes_token=tokens[yes_index],
            no_token=tokens[no_index],
            yes_ask=min(Decimal("0.999"), yes + spread / 2),
            no_ask=min(Decimal("0.999"), no + spread / 2),
            yes_bid=max(Decimal("0.001"), yes - spread / 2),
            no_bid=max(Decimal("0.001"), no - spread / 2),
            liquidity=liquidity,
            end_time=_time(raw.get("endDate")),
        )


class ClobClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.client = httpx.AsyncClient(
            base_url=settings.clob_url, timeout=20, transport=transport
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def executable_books(self, markets: list[Market]) -> list[Market]:
        if not markets:
            return []
        requested = [
            {"token_id": token}
            for market in markets
            for token in (market.yes_token, market.no_token)
        ]
        books: dict[str, dict[str, Any]] = {}
        for start in range(0, len(requested), 500):
            response = await self.client.post("/books", json=requested[start : start + 500])
            response.raise_for_status()
            books.update({str(book.get("asset_id")): book for book in response.json()})
        hydrated: list[Market] = []
        for market in markets:
            yes = books.get(market.yes_token)
            no = books.get(market.no_token)
            if not yes or not no:
                continue
            yes_bids, yes_asks = self._levels(yes)
            no_bids, no_asks = self._levels(no)
            if not yes_bids or not yes_asks or not no_bids or not no_asks:
                continue
            yes_ask = min(yes_asks, key=lambda level: level[0])
            no_ask = min(no_asks, key=lambda level: level[0])
            yes_bid = max(yes_bids, key=lambda level: level[0])
            no_bid = max(no_bids, key=lambda level: level[0])
            hydrated.append(
                Market(
                    condition_id=market.condition_id,
                    question=market.question,
                    yes_token=market.yes_token,
                    no_token=market.no_token,
                    yes_ask=yes_ask[0],
                    no_ask=no_ask[0],
                    yes_bid=yes_bid[0],
                    no_bid=no_bid[0],
                    liquidity=market.liquidity,
                    end_time=market.end_time,
                    yes_ask_size=yes_ask[1],
                    no_ask_size=no_ask[1],
                    min_order_size=max(
                        _decimal(yes.get("min_order_size")),
                        _decimal(no.get("min_order_size")),
                    ),
                    tick_size=max(
                        _decimal(yes.get("tick_size"), "0.01"),
                        _decimal(no.get("tick_size"), "0.01"),
                    ),
                )
            )
        return hydrated

    async def executable_groups(self, groups: list[MarketGroup]) -> list[MarketGroup]:
        all_markets = [market for group in groups for market in group.markets]
        hydrated = await self.executable_books(all_markets)
        by_condition = {market.condition_id: market for market in hydrated}
        result: list[MarketGroup] = []
        for group in groups:
            members = tuple(
                by_condition[market.condition_id]
                for market in group.markets
                if market.condition_id in by_condition
            )
            if len(members) == len(group.markets):
                result.append(
                    MarketGroup(
                        event_id=group.event_id,
                        title=group.title,
                        neg_risk_id=group.neg_risk_id,
                        markets=members,
                    )
                )
        return result

    @staticmethod
    def _levels(book: dict[str, Any]) -> tuple[list[tuple[Decimal, Decimal]], list[tuple[Decimal, Decimal]]]:
        def parse(raw: Any) -> list[tuple[Decimal, Decimal]]:
            return [
                (_decimal(level.get("price")), _decimal(level.get("size")))
                for level in raw or []
                if _decimal(level.get("price")) > 0 and _decimal(level.get("size")) > 0
            ]

        return parse(book.get("bids")), parse(book.get("asks"))
