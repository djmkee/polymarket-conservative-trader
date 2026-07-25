import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidURI

from .models import Market


def _decimal(value: Any, default: Decimal) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return default


class LiveBookCache:
    """Maintains executable top-of-book state from public WebSocket events."""

    def __init__(self, markets: list[Market]):
        self._markets = {market.condition_id: market for market in markets}
        self._tokens: dict[str, tuple[str, str]] = {}
        for market in markets:
            self._tokens[market.yes_token] = (market.condition_id, "YES")
            self._tokens[market.no_token] = (market.condition_id, "NO")

    @property
    def token_ids(self) -> list[str]:
        return list(self._tokens)

    def markets(self) -> list[Market]:
        return list(self._markets.values())

    def mark_stream_alive(self, observed_at: datetime | None = None) -> None:
        """Keep unchanged books fresh while their subscribed stream is healthy.

        A quiet book does not emit price events. Any message received on the
        active market subscription proves that the ordered stream is alive, so
        the cached top of book remains current even when its price is unchanged.
        """
        observed_at = observed_at or datetime.now(UTC)
        self._markets = {
            condition_id: replace(
                market,
                yes_updated_at=observed_at,
                no_updated_at=observed_at,
            )
            for condition_id, market in self._markets.items()
        }

    def apply(self, message: str | dict[str, Any] | list[Any]) -> list[dict[str, str]]:
        if isinstance(message, str):
            if message in {"PING", "PONG", "ping", "pong"}:
                return []
            try:
                message = json.loads(message)
            except json.JSONDecodeError:
                return []
        if isinstance(message, list):
            updates: list[dict[str, str]] = []
            for item in message:
                if isinstance(item, dict):
                    updates.extend(self._apply_event(item))
            return updates
        return self._apply_event(message)

    def _apply_event(self, raw: dict[str, Any]) -> list[dict[str, str]]:
        event_type = str(raw.get("event_type") or raw.get("type") or "")
        payload = raw.get("payload") if isinstance(raw.get("payload"), dict) else raw
        if event_type == "price_change":
            updates: list[dict[str, str]] = []
            for change in payload.get("price_changes") or payload.get("priceChanges") or []:
                if isinstance(change, dict):
                    updates.extend(self._apply_top(change, event_type))
            return updates
        if event_type == "book":
            token_id = str(
                payload.get("asset_id") or payload.get("assetId") or payload.get("tokenId") or ""
            )
            identity = self._tokens.get(token_id)
            if not identity:
                return []
            bids = self._levels(payload.get("bids"))
            asks = self._levels(payload.get("asks"))
            if not bids or not asks:
                return []
            bid = max(bids, key=lambda level: level[0])
            ask = min(asks, key=lambda level: level[0])
            return self._update(token_id, bid[0], ask[0], bid[1], ask[1], event_type)
        if event_type == "best_bid_ask":
            return self._apply_top(payload, event_type)
        if event_type == "tick_size_change":
            token_id = str(
                payload.get("asset_id") or payload.get("assetId") or payload.get("tokenId") or ""
            )
            identity = self._tokens.get(token_id)
            if not identity:
                return []
            condition_id, _ = identity
            market = self._markets[condition_id]
            tick = _decimal(
                payload.get("new_tick_size") or payload.get("newTickSize"),
                market.tick_size,
            )
            self._markets[condition_id] = replace(market, tick_size=tick)
        return []

    def _apply_top(self, payload: dict[str, Any], event_type: str) -> list[dict[str, str]]:
        token_id = str(
            payload.get("asset_id") or payload.get("assetId") or payload.get("tokenId") or ""
        )
        identity = self._tokens.get(token_id)
        if not identity:
            return []
        condition_id, outcome = identity
        market = self._markets[condition_id]
        old_bid = market.yes_bid if outcome == "YES" else market.no_bid
        old_ask = market.yes_ask if outcome == "YES" else market.no_ask
        bid = _decimal(payload.get("best_bid") or payload.get("bestBid"), old_bid)
        ask = _decimal(payload.get("best_ask") or payload.get("bestAsk"), old_ask)
        if bid <= 0 or ask <= bid or ask >= 1:
            return []
        return self._update(token_id, bid, ask, None, None, event_type)

    def _update(
        self,
        token_id: str,
        bid: Decimal,
        ask: Decimal,
        bid_size: Decimal | None,
        ask_size: Decimal | None,
        event_type: str,
    ) -> list[dict[str, str]]:
        condition_id, outcome = self._tokens[token_id]
        market = self._markets[condition_id]
        if outcome == "YES":
            market = replace(
                market,
                yes_bid=bid,
                yes_ask=ask,
                yes_bid_size=bid_size if bid_size is not None else market.yes_bid_size,
                yes_ask_size=ask_size if ask_size is not None else market.yes_ask_size,
                yes_updated_at=datetime.now(UTC),
            )
        else:
            market = replace(
                market,
                no_bid=bid,
                no_ask=ask,
                no_bid_size=bid_size if bid_size is not None else market.no_bid_size,
                no_ask_size=ask_size if ask_size is not None else market.no_ask_size,
                no_updated_at=datetime.now(UTC),
            )
        self._markets[condition_id] = market
        return [
            {
                "condition_id": condition_id,
                "token_id": token_id,
                "bid": str(bid),
                "ask": str(ask),
                "event_type": event_type,
            }
        ]

    @staticmethod
    def _levels(raw: Any) -> list[tuple[Decimal, Decimal]]:
        result: list[tuple[Decimal, Decimal]] = []
        for level in raw or []:
            if not isinstance(level, dict):
                continue
            price = _decimal(level.get("price"), Decimal(0))
            size = _decimal(level.get("size"), Decimal(0))
            if price > 0 and size > 0:
                result.append((price, size))
        return result


class MarketWebSocket:
    """Reconnectable public market stream with Polymarket's text heartbeat."""

    def __init__(self, url: str, token_ids: list[str]):
        self.url = url
        self.token_ids = token_ids

    async def messages(self) -> AsyncIterator[str]:
        backoff = 1
        while True:
            try:
                async with connect(
                    self.url,
                    ping_interval=None,
                    close_timeout=5,
                    open_timeout=20,
                    max_size=8 * 1024 * 1024,
                ) as socket:
                    await socket.send(
                        json.dumps(
                            {
                                "assets_ids": self.token_ids,
                                "type": "market",
                                "custom_feature_enabled": True,
                            }
                        )
                    )
                    heartbeat = asyncio.create_task(self._heartbeat(socket))
                    backoff = 1
                    try:
                        while True:
                            try:
                                yield await asyncio.wait_for(socket.recv(), timeout=20)
                            except TimeoutError:
                                # Give the engine a clock tick, then reconnect instead
                                # of hanging forever on a silent-but-open feed.
                                yield "__POLYBOT_STREAM_TIMEOUT__"
                                break
                    finally:
                        heartbeat.cancel()
                        await asyncio.gather(heartbeat, return_exceptions=True)
            except asyncio.CancelledError:
                raise
            except (ConnectionClosed, InvalidHandshake, InvalidURI, OSError, TimeoutError):
                yield "__POLYBOT_STREAM_RECONNECT__"
                await asyncio.sleep(backoff)
                backoff = min(30, backoff * 2)

    @staticmethod
    async def _heartbeat(socket: Any) -> None:
        while True:
            await asyncio.sleep(10)
            await socket.send("PING")
