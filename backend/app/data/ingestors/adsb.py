"""OpenSky HTTP boundary, based on the official OAuth2 REST documentation."""
from __future__ import annotations

import asyncio
import random
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from ...config import AdsBSettings

API_URL = "https://opensky-network.org/api/states/all"
TOKEN_URL = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"


class OpenSkyError(RuntimeError):
    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class OpenSkyClient:
    def __init__(self, settings: AdsBSettings, client: httpx.AsyncClient | None = None) -> None:
        self.settings, self.client = settings, client or httpx.AsyncClient()
        self._token: str | None = None
        self._expires_at = datetime.min.replace(tzinfo=timezone.utc)
        self.remaining_credits: int | None = None
        self.degraded = False

    async def close(self) -> None: await self.client.aclose()

    async def _token_headers(self, force: bool = False) -> dict[str, str]:
        if not self.settings.token_enabled:
            return {}
        now = datetime.now(timezone.utc)
        if force or not self._token or now >= self._expires_at:
            secret = self.settings.opensky_client_secret
            response = await self.client.post(TOKEN_URL, data={"grant_type": "client_credentials",
                "client_id": self.settings.opensky_client_id, "client_secret": secret.get_secret_value() if secret else ""},
                timeout=self.settings.request_timeout_seconds)
            response.raise_for_status()
            payload = response.json()
            self._token = payload["access_token"]
            self._expires_at = now + timedelta(seconds=max(1, int(payload.get("expires_in", 1800)) - 30))
        return {"Authorization": f"Bearer {self._token}"}

    @staticmethod
    def _retry_after(response: httpx.Response) -> float | None:
        for name in ("X-Rate-Limit-Retry-After-Seconds", "Retry-After"):
            value = response.headers.get(name)
            if value:
                try: return max(0.0, float(value))
                except ValueError: pass
        return None

    async def fetch_states(self) -> dict[str, Any]:
        """One request with 401 refresh and bounded handling for 429/5xx/timeouts."""
        refreshed = False
        attempt = 0
        while attempt <= self.settings.max_retries:
            try:
                response = await self.client.get(API_URL, params=self.settings.bounding_box.query_params(),
                    headers=await self._token_headers(), timeout=self.settings.request_timeout_seconds)
                if response.status_code == 401 and self.settings.token_enabled and not refreshed:
                    refreshed = True
                    await self._token_headers(force=True)
                    continue
                remaining = response.headers.get("X-Rate-Limit-Remaining")
                self.remaining_credits = int(remaining) if remaining and remaining.isdigit() else self.remaining_credits
                if response.status_code == 429:
                    delay = self._retry_after(response) or min(30.0, 2 ** attempt + random.random())
                    if attempt == self.settings.max_retries: raise OpenSkyError("OpenSky rate limited", retry_after=delay)
                    await asyncio.sleep(delay); attempt += 1; continue
                if response.status_code >= 500:
                    if attempt == self.settings.max_retries: response.raise_for_status()
                    await asyncio.sleep(min(30.0, 2 ** attempt + random.random())); attempt += 1; continue
                response.raise_for_status()
                self.degraded = False
                result = response.json()
                if result.get("states") is None: result["states"] = []
                return result
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt == self.settings.max_retries:
                    self.degraded = True
                    raise OpenSkyError("OpenSky transport failure") from exc
                await asyncio.sleep(min(30.0, 2 ** attempt + random.random()))
                attempt += 1
        raise AssertionError("unreachable")
