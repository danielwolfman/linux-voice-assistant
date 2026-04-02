"""Listen for Home Assistant-managed runtime configuration updates."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Optional

import aiohttp

_LOGGER = logging.getLogger(__name__)

_REMOTE_KEYS = {
    "openai_model": str,
    "openai_voice": str,
    "openai_instructions": str,
    "wakeup_sound": str,
    "processing_sound": str,
    "tool_call_sound": str,
    "session_end_sound": str,
    "session_timeout_seconds": float,
    "vad_threshold": float,
    "min_speech_seconds": float,
    "end_silence_seconds": float,
    "refractory_seconds": float,
    "follow_up_after_tool_call": bool,
    "enable_tool_get_entities": bool,
    "enable_tool_get_state": bool,
    "enable_tool_call_service": bool,
    "enable_tool_web_search": bool,
}


class HomeAssistantSettingsListener:
    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        verify_ssl: bool,
        on_update: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._verify_ssl = verify_ssl
        self._on_update = on_update
        self._session: Optional[aiohttp.ClientSession] = None
        self._task: Optional[asyncio.Task[None]] = None
        self._settings_entity_id: Optional[str] = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        await self._load_initial_settings()
        self._task = asyncio.create_task(self._listen_forever())

    async def close(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def _load_initial_settings(self) -> None:
        try:
            states = await self._request_json("GET", "/api/states")
        except Exception:
            _LOGGER.exception("Failed to load initial realtime satellite settings from Home Assistant")
            return

        if not isinstance(states, list):
            return

        for state in states:
            settings = _extract_settings_from_state(state)
            if settings is not None:
                self._settings_entity_id = str(state.get("entity_id", ""))
                _LOGGER.debug("Discovered Home Assistant settings entity: %s", self._settings_entity_id)
                await self._on_update(settings)
                return

    async def _listen_forever(self) -> None:
        while self._running:
            try:
                await self._listen_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.exception("Home Assistant settings listener disconnected, retrying")
                await asyncio.sleep(2.0)

    async def _listen_once(self) -> None:
        websocket_url = self._base_url.replace("http://", "ws://").replace("https://", "wss://") + "/api/websocket"
        session = await self._session_or_create()
        async with session.ws_connect(websocket_url, ssl=self._verify_ssl) as websocket:
            await websocket.receive_json()
            await websocket.send_json({"type": "auth", "access_token": self._token})
            auth_response = await websocket.receive_json()
            if auth_response.get("type") != "auth_ok":
                raise RuntimeError("Home Assistant websocket authentication failed for settings listener")

            await websocket.send_json({"id": 1, "type": "subscribe_events", "event_type": "state_changed"})
            while self._running:
                message = await websocket.receive_json()
                if message.get("id") == 1 and message.get("type") == "result":
                    continue
                if message.get("type") != "event":
                    continue
                event = message.get("event") or {}
                data = event.get("data") or {}
                new_state = data.get("new_state")
                if new_state is None:
                    continue

                settings = _extract_settings_from_state(new_state)
                entity_id = str(new_state.get("entity_id", ""))
                if settings is None:
                    continue

                if self._settings_entity_id is None:
                    self._settings_entity_id = entity_id
                if entity_id != self._settings_entity_id:
                    continue

                _LOGGER.debug("Received Home Assistant settings update from %s", entity_id)
                await self._on_update(settings)

    async def _request_json(self, method: str, path: str) -> Any:
        session = await self._session_or_create()
        async with session.request(method, self._base_url + path, ssl=self._verify_ssl) as response:
            response.raise_for_status()
            return await response.json()

    async def _session_or_create(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json",
                }
            )
        return self._session


def _extract_settings_from_state(state: Any) -> Optional[dict[str, Any]]:
    if not isinstance(state, dict):
        return None
    attributes = state.get("attributes")
    if not isinstance(attributes, dict):
        return None
    if attributes.get("integration_domain") != "realtime_satellite" or not attributes.get("settings_entity"):
        return None

    settings: dict[str, Any] = {}
    for key, value_type in _REMOTE_KEYS.items():
        if key not in attributes:
            continue
        value = attributes[key]
        try:
            if value_type is bool:
                settings[key] = bool(value)
            else:
                settings[key] = value_type(value)
        except (TypeError, ValueError):
            _LOGGER.warning("Ignoring invalid Home Assistant settings value for %s: %r", key, value)
    return settings
