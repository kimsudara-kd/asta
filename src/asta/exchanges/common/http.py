"""Egress-checked public HTTP client shared by adapters."""
from __future__ import annotations

from typing import Any

import httpx

from asta.core.egress import assert_url_allowed
from asta.core.money import loads_decimal


class PublicHttp:
    def __init__(self, base_url: str, client: httpx.AsyncClient | None = None,
                 timeout: float = 10.0) -> None:
        assert_url_allowed(base_url)
        self._base = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=timeout)

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self._base}{path}"
        assert_url_allowed(url)  # also blocks private/trading paths
        resp = await self._client.get(url, params=params)
        if resp.status_code in (418, 429):
            retry = resp.headers.get("Retry-After", "?")
            raise RateLimited(f"{resp.status_code} from {url}; Retry-After={retry}")
        resp.raise_for_status()
        return loads_decimal(resp.content)

    async def aclose(self) -> None:
        await self._client.aclose()


class RateLimited(Exception):
    """HTTP 418/429: caller must back off; never retry in a tight loop."""
