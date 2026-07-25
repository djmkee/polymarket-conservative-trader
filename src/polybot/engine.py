import asyncio
import time
from dataclasses import replace
from decimal import Decimal
from typing import Any

from .config import Settings
from .market_data import ClobClient, GammaClient
from .market_maker import PaperMarketMaker
from .models import Portfolio
from .realtime import LiveBookCache, MarketWebSocket
from .risk import RiskManager
from .store import AuditStore
from .strategies import CompleteSetArbitrage, NearResolutionCandidate, NegativeRiskArbitrage


class Engine:
    def __init__(self, settings: Settings):
        self.s = settings
        self.data = GammaClient(settings)
        self.clob = ClobClient(settings)
        self.risk = RiskManager(settings)
        self.store = AuditStore(settings.db_path)
        self.arb = CompleteSetArbitrage()
        self.review = NearResolutionCandidate()
        self.neg_risk = NegativeRiskArbitrage()
        self.maker = PaperMarketMaker(settings, self.store)
        self.portfolio = Portfolio(
            cash=settings.initial_equity,
            peak_equity=settings.initial_equity,
            day_start_equity=settings.initial_equity,
        )

    async def close(self) -> None:
        await self.data.close()
        await self.clob.close()
        self.store.close()

    async def cycle(self) -> dict[str, Any]:
        discovered = await self.data.active_binary_markets()
        markets = await self.clob.executable_books(discovered)
        discovered_groups = await self.data.active_negative_risk_groups()
        groups = await self.clob.executable_groups(discovered_groups)
        maker_result = self.maker.run(markets) if self.s.maker_enabled else {}
        candidates = orders = reviews = 0
        group_candidates = 0
        best_group_edge: Decimal | None = None
        # Includes configured slippage; category-specific fees must be fetched
        # from the CLOB before executable arbitrage is enabled.
        cost_buffer = self.s.slippage_bps / Decimal(10000)
        for market in markets:
            arb_signals = self.arb.evaluate(market, cost_buffer)
            if arb_signals:
                candidates += 1
                intents = [self.risk.size(signal, self.portfolio) for signal in arb_signals]
                # Atomicity rule: never take only one leg of a complete set.
                if all(intents):
                    executable_shares = min(
                        market.yes_ask_size,
                        market.no_ask_size,
                        *(intent.shares for intent in intents if intent),
                    )
                    if executable_shares < market.min_order_size:
                        continue
                    intents = [
                        replace(
                            intent,
                            shares=executable_shares,
                            notional=executable_shares * intent.signal.price,
                        )
                        for intent in intents
                        if intent
                    ]
                    orders += 2
                    self.store.record(
                        "paper_arb_candidate",
                        {
                            "market": market.question,
                            "condition_id": market.condition_id,
                            "legs": [intent.__dict__ for intent in intents if intent],
                        },
                    )
            for signal in self.review.evaluate(market):
                reviews += 1
                self.store.record("manual_review_candidate", signal.__dict__)
        for group in groups:
            group_edge = self.neg_risk.basket_edge(group, cost_buffer)
            best_group_edge = (
                group_edge if best_group_edge is None else max(best_group_edge, group_edge)
            )
            signals = self.neg_risk.evaluate(group, cost_buffer)
            if not signals:
                continue
            group_candidates += 1
            basket_cost = sum((signal.price for signal in signals), Decimal(0))
            budget = min(
                self.portfolio.equity * self.s.max_market_exposure_pct,
                self.portfolio.cash,
            )
            shares = budget / basket_cost
            shares = min(shares, *(market.yes_ask_size for market in group.markets))
            if any(shares < market.min_order_size for market in group.markets):
                continue
            orders += len(signals)
            self.store.record(
                "paper_neg_risk_candidate",
                {
                    "event_id": group.event_id,
                    "event": group.title,
                    "outcomes": len(group.markets),
                    "shares_per_outcome": shares,
                    "basket_cost": shares * basket_cost,
                    "locked_payout": shares,
                    "projected_profit": shares * (Decimal(1) - basket_cost),
                    "legs": [
                        {
                            "question": market.question,
                            "token_id": market.yes_token,
                            "price": market.yes_ask,
                        }
                        for market in group.markets
                    ],
                },
            )
        self.store.record(
            "cycle",
            {
                "discovered_markets": len(discovered),
                "executable_markets": len(markets),
                "arb_candidates": candidates,
                "paper_orders": orders,
                "negative_risk_groups": len(groups),
                "negative_risk_candidates": group_candidates,
                "best_negative_risk_edge": best_group_edge,
                **maker_result,
            },
        )
        return {
            "discovered": len(discovered),
            "executable": len(markets),
            "candidates": candidates,
            "orders": orders,
            "reviews": reviews,
            "groups": len(groups),
            "group_candidates": group_candidates,
            "best_group_edge": (
                f"{best_group_edge:.4%}" if best_group_edge is not None else "n/a"
            ),
            **maker_result,
        }

    async def stream(self, duration_seconds: int = 0) -> dict[str, Any]:
        """Run the public WebSocket paper engine; zero duration means until stopped."""
        started = time.monotonic()
        sessions = messages = updates = quote_cycles = 0
        latest: dict[str, Any] = {}
        while duration_seconds <= 0 or time.monotonic() - started < duration_seconds:
            discovered = await self.data.active_binary_markets()
            markets = await self.clob.executable_books(discovered)
            markets.sort(key=lambda market: market.liquidity, reverse=True)
            preferred = (
                self.maker.selected_markets(markets) if self.s.maker_enabled else []
            )
            preferred_ids = {market.condition_id for market in preferred}
            tracked = (
                preferred
                + [
                    market
                    for market in markets
                    if market.condition_id not in preferred_ids
                ]
            )[: self.s.realtime_max_markets]
            if not tracked:
                self.store.record("realtime_empty", {"discovered": len(discovered)})
                return {
                    "sessions": sessions,
                    "messages": messages,
                    "book_updates": updates,
                    "quote_cycles": quote_cycles,
                    "reason": "no_executable_markets",
                }
            cache = LiveBookCache(tracked)
            socket = MarketWebSocket(self.s.realtime_ws_url, cache.token_ids)
            sessions += 1
            latest = self.maker.run(cache.markets()) if self.s.maker_enabled else {}
            quote_cycles += 1
            self.store.record(
                "realtime_session",
                {
                    "session": sessions,
                    "tracked_markets": len(tracked),
                    "tracked_tokens": len(cache.token_ids),
                },
            )
            session_started = time.monotonic()
            next_quote = session_started + self.s.realtime_quote_interval_seconds
            remaining = (
                max(0.1, duration_seconds - (time.monotonic() - started))
                if duration_seconds > 0
                else self.s.realtime_refresh_seconds
            )
            session_limit = min(self.s.realtime_refresh_seconds, remaining)
            try:
                async with asyncio.timeout(session_limit):
                    async for message in socket.messages():
                        messages += 1
                        if isinstance(message, str) and message.startswith(
                            "__POLYBOT_STREAM_"
                        ):
                            refreshed = await self.clob.executable_books(cache.markets())
                            if refreshed:
                                cache = LiveBookCache(refreshed)
                                for market in refreshed:
                                    for token_id, bid, ask in (
                                        (
                                            market.yes_token,
                                            market.yes_bid,
                                            market.yes_ask,
                                        ),
                                        (
                                            market.no_token,
                                            market.no_bid,
                                            market.no_ask,
                                        ),
                                    ):
                                        self.store.record_tick(
                                            market.condition_id,
                                            token_id,
                                            str(bid),
                                            str(ask),
                                            "rest_fallback",
                                        )
                        changed = cache.apply(message)
                        for update in changed:
                            self.store.record_tick(
                                update["condition_id"],
                                update["token_id"],
                                update["bid"],
                                update["ask"],
                                update["event_type"],
                            )
                        updates += len(changed)
                        now = time.monotonic()
                        if now >= next_quote and self.s.maker_enabled:
                            latest = self.maker.run(cache.markets())
                            quote_cycles += 1
                            self.store.record("realtime_cycle", latest)
                            next_quote = now + self.s.realtime_quote_interval_seconds
            except TimeoutError:
                if duration_seconds > 0 and time.monotonic() - started >= duration_seconds:
                    return {
                        "sessions": sessions,
                        "messages": messages,
                        "book_updates": updates,
                        "quote_cycles": quote_cycles,
                        **latest,
                    }
        return {
            "sessions": sessions,
            "messages": messages,
            "book_updates": updates,
            "quote_cycles": quote_cycles,
            **latest,
        }
