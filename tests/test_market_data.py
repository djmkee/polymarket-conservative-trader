import json

import httpx

from polybot.config import Settings
from polybot.market_data import GammaClient


async def test_discovery_requests_most_liquid_markets_first():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["order"] == "liquidityNum"
        assert request.url.params["ascending"] == "false"
        assert request.url.params["offset"] == "0"
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


async def test_discovery_accepts_non_yes_no_binary_labels():
    async def handler(request: httpx.Request) -> httpx.Response:
        market = {
            "conditionId": "condition",
            "question": "Up or down?",
            "outcomes": json.dumps(["Up", "Down"]),
            "clobTokenIds": json.dumps(["up-token", "down-token"]),
            "outcomePrices": json.dumps(["0.48", "0.52"]),
            "liquidityNum": 20_000,
        }
        return httpx.Response(200, json=[market])

    client = GammaClient(Settings(), transport=httpx.MockTransport(handler))
    try:
        markets = await client.active_binary_markets(limit=5)
    finally:
        await client.close()

    assert len(markets) == 1
