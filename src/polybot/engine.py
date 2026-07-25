from dataclasses import replace
from decimal import Decimal

from .config import Settings
from .market_data import ClobClient, GammaClient
from .models import Portfolio
from .risk import RiskManager
from .store import AuditStore
from .strategies import CompleteSetArbitrage, NearResolutionCandidate


class Engine:
    def __init__(self, settings: Settings):
        self.s = settings
        self.data = GammaClient(settings)
        self.clob = ClobClient(settings)
        self.risk = RiskManager(settings)
        self.store = AuditStore(settings.db_path)
        self.arb = CompleteSetArbitrage()
        self.review = NearResolutionCandidate()
        self.portfolio = Portfolio(
            cash=settings.initial_equity,
            peak_equity=settings.initial_equity,
            day_start_equity=settings.initial_equity,
        )

    async def close(self) -> None:
        await self.data.close()
        await self.clob.close()
        self.store.close()

    async def cycle(self) -> dict[str, int]:
        discovered = await self.data.active_binary_markets()
        markets = await self.clob.executable_books(discovered)
        candidates = orders = reviews = 0
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
        self.store.record(
            "cycle",
            {
                "discovered_markets": len(discovered),
                "executable_markets": len(markets),
                "arb_candidates": candidates,
                "paper_orders": orders,
            },
        )
        return {
            "discovered": len(discovered),
            "executable": len(markets),
            "candidates": candidates,
            "orders": orders,
            "reviews": reviews,
        }
