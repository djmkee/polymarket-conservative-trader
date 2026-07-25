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

    async def active_binary_markets(self, limit: int = 100) -> list[Market]:
        response = await self.client.get(
            "/markets",
            params={
                "active": "true",
                "closed": "false",
                "limit": limit,
                "order": "liquidityNum",
                "ascending": "false",
            },
        )
        response.raise_for_status()
        result: list[Market] = []
        for raw in response.json():
            outcomes = [str(x).upper() for x in _jsonish(raw.get("outcomes"))]
            tokens = [str(x) for x in _jsonish(raw.get("clobTokenIds"))]
            prices = [_decimal(x) for x in _jsonish(raw.get("outcomePrices"))]
            if outcomes != ["YES", "NO"] or len(tokens) != 2 or len(prices) != 2:
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
