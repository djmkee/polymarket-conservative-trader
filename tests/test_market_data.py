import json

import httpx

from polybot.config import Settings
from polybot.market_data import GammaClient


async def test_discovery_requests_most_liquid_markets_first():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["order"] == "liquidityNum"
        assert request.url.params["ascending"] == "false"
        market = {
            "conditionId": "condition",
            "question": "Test market?",
            "outcomes": json.dumps(["Yes", "No"]),
            "clobTokenIds": json.dumps(["yes-token", "no-token"]),
            "outcomePrices": json.dumps(["0.48", "0.52"]),
            "liquidityNum": 20_000,
            "endDate": "2026-08-01T00:00:00Z",
        }
        return httpx.Response(200, json=[market])

    client = GammaClient(Settings(), transport=httpx.MockTransport(handler))
    try:
        markets = await client.active_binary_markets(limit=5)
    finally:
        await client.close()

    assert len(markets) == 1
    assert markets[0].condition_id == "condition"
