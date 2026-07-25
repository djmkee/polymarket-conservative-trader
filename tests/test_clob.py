from datetime import UTC, datetime
from decimal import Decimal

import httpx

from polybot.config import Settings
from polybot.market_data import ClobClient
from polybot.models import Market


async def test_clob_uses_executable_best_prices_and_sizes():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/books"
        return httpx.Response(
            200,
            json=[
                {
                    "asset_id": "y",
                    "bids": [{"price": ".45", "size": "20"}, {"price": ".47", "size": "10"}],
                    "asks": [{"price": ".51", "size": "40"}, {"price": ".49", "size": "7"}],
                    "min_order_size": "5",
                },
                {
                    "asset_id": "n",
                    "bids": [{"price": ".48", "size": "20"}],
                    "asks": [{"price": ".50", "size": "9"}],
                    "min_order_size": "5",
                },
            ],
        )

    client = ClobClient(Settings(), transport=httpx.MockTransport(handler))
    try:
        hydrated = await client.executable_books(
            [
                Market(
                    condition_id="c",
                    question="Test?",
                    yes_token="y",
                    no_token="n",
                    yes_ask=Decimal(".5"),
                    no_ask=Decimal(".5"),
                    yes_bid=Decimal(".49"),
                    no_bid=Decimal(".49"),
                    liquidity=Decimal(20_000),
                    end_time=datetime.now(UTC),
                )
            ]
        )
    finally:
        await client.close()

    assert hydrated[0].yes_ask == Decimal(".49")
    assert hydrated[0].yes_bid == Decimal(".47")
    assert hydrated[0].yes_ask_size == Decimal(7)
    assert hydrated[0].no_ask_size == Decimal(9)
    assert hydrated[0].min_order_size == Decimal(5)
