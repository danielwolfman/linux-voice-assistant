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

_UNIQUE_ID_TO_KEY = {f"openai_real_time_assistant_{key}": key for key in _REMOTE_KEYS}


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
        self._running = False
        self._entity_ids_by_key: dict[str, str] = {}

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        await self._discover_entities()
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

    async def _discover_entities(self) -> None:
        try:
            entity_registry = await self._ws_command("config/entity_registry/list")
        except Exception:
            _LOGGER.exception("Failed to discover Home Assistant setting entities")
            return

        discovered: dict[str, str] = {}
        for entry in entity_registry:
            if not isinstance(entry, dict):
                continue
            unique_id = str(entry.get("unique_id") or "")
            key = _UNIQUE_ID_TO_KEY.get(unique_id)
            if not key:
                continue
            entity_id = str(entry.get("entity_id") or "")
            if entity_id:
                discovered[key] = entity_id

        self._entity_ids_by_key = discovered
        _LOGGER.debug("Discovered Home Assistant setting entities: %s", discovered)

    async def _load_initial_settings(self) -> None:
        settings: dict[str, Any] = {}
        for key, entity_id in self._entity_ids_by_key.items():
            try:
                state = await self._request_json("GET", f"/api/states/{entity_id}")
            except Exception:
                _LOGGER.exception("Failed to load Home Assistant setting state for %s", entity_id)
                continue
            parsed = _parse_entity_state(key, state)
            if parsed is not None:
                settings[key] = parsed

        if settings:
            await self._on_update(settings)

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

                entity_id = str(new_state.get("entity_id") or "")
                key = _key_for_entity_id(entity_id, self._entity_ids_by_key)
                if key is None:
                    continue

                parsed = _parse_entity_state(key, new_state)
                if parsed is None:
                    continue
                await self._on_update({key: parsed})

    async def _request_json(self, method: str, path: str) -> Any:
        session = await self._session_or_create()
        async with session.request(method, self._base_url + path, ssl=self._verify_ssl) as response:
            response.raise_for_status()
            return await response.json()

    async def _ws_command(self, command_type: str) -> Any:
        session = await self._session_or_create()
        websocket_url = self._base_url.replace("http://", "ws://").replace("https://", "wss://") + "/api/websocket"
        async with session.ws_connect(websocket_url, ssl=self._verify_ssl) as websocket:
            await websocket.receive_json()
            await websocket.send_json({"type": "auth", "access_token": self._token})
            auth_response = await websocket.receive_json()
            if auth_response.get("type") != "auth_ok":
                raise RuntimeError("Home Assistant websocket authentication failed for settings listener")
            await websocket.send_json({"id": 1, "type": command_type})
            while True:
                message = await websocket.receive_json()
                if message.get("id") != 1:
                    continue
                if not message.get("success", False):
                    raise RuntimeError(f"Home Assistant websocket command failed: {command_type}")
                return message.get("result")

    async def _session_or_create(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json",
                }
            )
        return self._session


def _key_for_entity_id(entity_id: str, entity_ids_by_key: dict[str, str]) -> Optional[str]:
    for key, known_entity_id in entity_ids_by_key.items():
        if entity_id == known_entity_id:
            return key
    return None


def _parse_entity_state(key: str, state: Any) -> Optional[Any]:
    if not isinstance(state, dict):
        return None
    raw_state = state.get("state")
    value_type = _REMOTE_KEYS[key]
    try:
        if value_type is bool:
            return str(raw_state).lower() == "on"
        return value_type(raw_state)
    except (TypeError, ValueError):
        _LOGGER.warning("Ignoring invalid Home Assistant setting state for %s: %r", key, raw_state)
        return None
