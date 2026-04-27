"""
polymarket.py — Async wrappers around Polymarket's public APIs

Gamma API   https://gamma-api.polymarket.com  -> market metadata, prices, liquidity
Data API    https://data-api.polymarket.com   -> trades, positions, activity (fully public)
CLOB API    https://clob.polymarket.com       -> orderbook (public read)
"""

import asyncio
import aiohttp
import logging
from typing import Optional

logger = logging.getLogger(__name__)

DATA_API = "https://data-api.polymarket.com"


class PolymarketClient:
    def __init__(self, gamma_base: str, clob_base: str):
        self.gamma_base = gamma_base.rstrip("/")
        self.clob_base = clob_base.rstrip("/")
        self._session: Optional[aiohttp.ClientSession] = None

    async def _session_(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=15)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def _get(self, base: str, path: str, params: dict = None) -> dict | list | None:
        session = await self._session_()
        url = f"{base}{path}"
        try:
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    return await resp.json(content_type=None)
                elif resp.status == 429:
                    logger.warning("Rate limited by Polymarket. Sleeping 30s...")
                    await asyncio.sleep(30)
                else:
                    logger.warning(f"HTTP {resp.status} from {url}")
        except asyncio.TimeoutError:
            logger.warning(f"Timeout fetching {url}")
        except Exception as e:
            logger.error(f"Error fetching {url}: {e}")
        return None

    # Gamma API - Markets

    async def get_active_markets(self, limit: int = 100, offset: int = 0) -> list[dict]:
        data = await self._get(
            self.gamma_base,
            "/markets",
            params={
                "active": "true",
                "closed": "false",
                "limit": limit,
                "offset": offset,
                "order": "volume24hr",
                "ascending": "false",
            },
        )
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "markets" in data:
            return data["markets"]
        return []

    async def get_market(self, condition_id: str) -> dict | None:
        return await self._get(self.gamma_base, f"/markets/{condition_id}")

    # Data API - Trades (fully public)

    async def get_recent_trades(self, market_id: str, limit: int = 50, after_ts: int = None) -> list[dict]:
        params: dict = {"market": market_id, "limit": limit}
        if after_ts:
            params["after"] = after_ts
        data = await self._get(DATA_API, "/trades", params=params)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "data" in data:
            return data["data"]
        return []

    async def get_wallet_trades(self, address: str, limit: int = 100) -> list[dict]:
        data = await self._get(DATA_API, "/activity", params={"user": address, "limit": limit})
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "data" in data:
            return data["data"]
        return []

    async def get_positions(self, market_id: str) -> list[dict]:
        data = await self._get(DATA_API, "/positions", params={"market": market_id, "limit": 100})
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "data" in data:
            return data["data"]
        return []

    async def get_order_book(self, token_id: str) -> dict | None:
        return await self._get(self.clob_base, "/book", params={"token_id": token_id})
