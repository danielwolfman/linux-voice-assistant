"""Send satellite activity events into Home Assistant."""

from __future__ import annotations

import logging
from typing import Any, Optional

import aiohttp

_LOGGER = logging.getLogger(__name__)


class HomeAssistantActivityLogger:
    def __init__(self, base_url: str, token: str, verify_ssl: bool = True) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._verify_ssl = verify_ssl
        self._session: Optional[aiohttp.ClientSession] = None

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def record_activity(self, category: str, message: str, details: dict[str, Any] | None = None) -> None:
        payload = {"category": category, "message": message, "details": details or {}}
        await self._post_service("record_activity", payload)

    async def _post_service(self, service: str, payload: dict[str, Any]) -> None:
        session = await self._session_or_create()
        try:
            async with session.post(
                f"{self._base_url}/api/services/openai_real_time_assistant/{service}",
                json=payload,
                ssl=self._verify_ssl,
            ) as response:
                response.raise_for_status()
                await response.read()
        except Exception:
            _LOGGER.exception("Failed posting %s to Home Assistant activity logger", service)

    async def _session_or_create(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json",
                }
            )
        return self._session
