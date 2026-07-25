import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from .config import Settings
from .models import Market


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
            outcomes = [str(x).upper() for x in _jsonish(raw.get("outcomes"))]
            tokens = [str(x) for x in _jsonish(raw.get("clobTokenIds"))]
            prices = [_decimal(x) for x in _jsonish(raw.get("outcomePrices"))]
            if len(outcomes) != 2 or len(tokens) != 2 or len(prices) != 2:
                continue
            liquidity = _decimal(raw.get("liquidityNum", raw.get("liquidity", 0)))
            if liquidity < self.settings.min_liquidity:
                continue
            # Gamma midpoint is a discovery fallback. The execution scanner will
            # replace these with CLOB bid/ask snapshots before any order decision.
            yes, no = prices
            spread = Decimal("0.01")
            result.append(
                Market(
                    condition_id=str(raw.get("conditionId", "")),
                    question=str(raw.get("question", "")),
                    yes_token=tokens[0],
                    no_token=tokens[1],
                    yes_ask=min(Decimal("0.999"), yes + spread / 2),
                    no_ask=min(Decimal("0.999"), no + spread / 2),
                    yes_bid=max(Decimal("0.001"), yes - spread / 2),
                    no_bid=max(Decimal("0.001"), no - spread / 2),
                    liquidity=liquidity,
                    end_time=_time(raw.get("endDate")),
                )
            )
        return result


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
                )
            )
        return hydrated

    @staticmethod
    def _levels(book: dict[str, Any]) -> tuple[list[tuple[Decimal, Decimal]], list[tuple[Decimal, Decimal]]]:
        def parse(raw: Any) -> list[tuple[Decimal, Decimal]]:
            return [
                (_decimal(level.get("price")), _decimal(level.get("size")))
                for level in raw or []
                if _decimal(level.get("price")) > 0 and _decimal(level.get("size")) > 0
            ]

        return parse(book.get("bids")), parse(book.get("asks"))
