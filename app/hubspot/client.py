"""Async HubSpot REST client: bearer auth, bounded concurrency, retry/backoff
that honors Retry-After / rate-limit headers, and a request counter so the
sync can prove it stayed fast.
"""
import asyncio
from typing import Any, Dict, List, Optional

import httpx

from ..config import settings


class HubSpotError(Exception):
    pass


class HubSpotClient:
    def __init__(
        self,
        token: Optional[str] = None,
        base_url: Optional[str] = None,
        concurrency: int = 5,
        timeout: float = 30.0,
        max_retries: int = 6,
    ) -> None:
        self.base_url = (base_url or settings.HUBSPOT_BASE_URL).rstrip("/")
        self.token = token or settings.HUBSPOT_ACCESS_TOKEN
        self.max_retries = max_retries
        self.request_count = 0
        self._sem = asyncio.Semaphore(concurrency)
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )

    async def __aenter__(self) -> "HubSpotClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, *, json: Any = None,
                       params: Any = None) -> Dict[str, Any]:
        delay = 1.0
        last_text = ""
        for attempt in range(self.max_retries):
            async with self._sem:
                self.request_count += 1
                try:
                    resp = await self._client.request(method, path, json=json, params=params)
                except httpx.HTTPError as e:  # network/timeout -> retry
                    last_text = str(e)
                    resp = None
            if resp is None:
                await asyncio.sleep(min(delay, 15))
                delay = min(delay * 2, 15)
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                retry_after = resp.headers.get("Retry-After")
                wait = float(retry_after) if retry_after else delay
                last_text = f"{resp.status_code} {resp.text[:200]}"
                await asyncio.sleep(min(wait, 15))
                delay = min(delay * 2, 15)
                continue
            if resp.status_code >= 400:
                raise HubSpotError(f"{resp.status_code} {method} {path}: {resp.text[:400]}")
            if not resp.content:
                return {}
            return resp.json()
        raise HubSpotError(f"max retries exhausted for {method} {path}: {last_text}")

    async def get(self, path: str, params: Any = None) -> Dict[str, Any]:
        return await self._request("GET", path, params=params)

    async def post(self, path: str, json: Any = None) -> Dict[str, Any]:
        return await self._request("POST", path, json=json)
