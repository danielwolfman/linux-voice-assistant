"""Curated Home Assistant tool bridge."""

from __future__ import annotations

import logging
import ssl
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urljoin

import aiohttp

_LOGGER = logging.getLogger(__name__)


@dataclass
class EntityRecord:
    entity_id: str
    name: str
    state: str
    domain: str
    area: Optional[str]
    attributes: dict[str, Any]

    def as_tool_result(self) -> dict[str, Any]:
        suggested_services = _suggested_services(self.domain)
        return {
            "entity_id": self.entity_id,
            "name": self.name,
            "state": self.state,
            "domain": self.domain,
            "area": self.area,
            "attributes": _curated_attributes(self.attributes),
            "suggested_service_domain": self.domain if suggested_services else None,
            "suggested_services": suggested_services,
        }


class HomeAssistantToolBridge:
    def __init__(self, base_url: str, token: str, verify_ssl: bool = True) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._verify_ssl = verify_ssl
        self._session: Optional[aiohttp.ClientSession] = None
        self._states_cache: Optional[list[dict[str, Any]]] = None
        self._areas_by_id: dict[str, str] = {}
        self._entity_area_by_id: dict[str, str] = {}

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    def tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "name": "get_entities",
                "description": "Search Home Assistant entities by name, room, area, or domain. Prefer this first for natural-language device requests.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Natural-language search phrase like office light, kitchen lamp, or bedroom AC."},
                        "area": {"type": "string", "description": "Optional exact Home Assistant area name like Kitchen or Living Room when you are confident."},
                        "domain": {"type": "string", "description": "Entity domain like light, climate, switch, or sensor."},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 25, "default": 10},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "type": "function",
                "name": "get_state",
                "description": "Get the current state of a Home Assistant entity.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "entity_id": {"type": "string", "description": "Exact Home Assistant entity_id."},
                    },
                    "required": ["entity_id"],
                    "additionalProperties": False,
                },
            },
            {
                "type": "function",
                "name": "call_service",
                "description": "Call a Home Assistant service on one or more targets.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "domain": {"type": "string", "description": "Service domain like light, climate, scene, or script."},
                        "service": {"type": "string", "description": "Service name like turn_on, turn_off, toggle, or set_temperature."},
                        "target": {
                            "type": "object",
                            "properties": {
                                "entity_id": {
                                    "oneOf": [
                                        {"type": "string"},
                                        {"type": "array", "items": {"type": "string"}},
                                    ]
                                },
                                "area_id": {
                                    "oneOf": [
                                        {"type": "string"},
                                        {"type": "array", "items": {"type": "string"}},
                                    ]
                                },
                                "device_id": {
                                    "oneOf": [
                                        {"type": "string"},
                                        {"type": "array", "items": {"type": "string"}},
                                    ]
                                },
                            },
                            "additionalProperties": True,
                        },
                        "data": {"type": "object", "additionalProperties": True},
                    },
                    "required": ["domain", "service"],
                    "additionalProperties": False,
                },
            },
        ]

    async def execute_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        _LOGGER.debug("HA tool requested: %s args=%s", name, arguments)
        if name == "get_entities":
            result = await self.get_entities(
                query=arguments.get("query"),
                area=arguments.get("area"),
                domain=arguments.get("domain"),
                limit=int(arguments.get("limit", 10)),
            )
            _LOGGER.debug("HA tool result: %s summary=%s", name, _summarize_tool_result(result))
            return result
        if name == "get_state":
            result = await self.get_state(str(arguments["entity_id"]))
            _LOGGER.debug("HA tool result: %s summary=%s", name, _summarize_tool_result(result))
            return result
        if name == "call_service":
            result = await self.call_service(
                domain=str(arguments["domain"]),
                service=str(arguments["service"]),
                target=arguments.get("target") or {},
                data=arguments.get("data") or {},
            )
            _LOGGER.debug("HA tool result: %s summary=%s", name, _summarize_tool_result(result))
            return result
        raise ValueError(f"Unsupported Home Assistant tool: {name}")

    async def get_entities(self, query: Optional[str] = None, area: Optional[str] = None, domain: Optional[str] = None, limit: int = 10) -> dict[str, Any]:
        entities = await self._entity_records()
        normalized_query = (query or "").strip().lower()
        normalized_area = (area or "").strip().lower()
        normalized_domain = (domain or "").strip().lower()

        filtered = []
        for entity in entities:
            if normalized_domain and entity.domain != normalized_domain:
                continue
            if normalized_area and not _matches_area(entity.area, normalized_area):
                continue
            if normalized_query and not _matches_query(entity, normalized_query):
                continue
            filtered.append(entity)

        filtered.sort(key=lambda entity: _entity_match_score(entity, normalized_query), reverse=True)
        limited = filtered[: max(1, min(limit, 25))]
        _LOGGER.debug(
            "HA get_entities query=%r area=%r domain=%r matches=%s",
            query,
            area,
            domain,
            [entity.entity_id for entity in limited],
        )
        return {
            "count": len(limited),
            "entities": [entity.as_tool_result() for entity in limited],
        }

    async def get_state(self, entity_id: str) -> dict[str, Any]:
        for entity in await self._entity_records():
            if entity.entity_id == entity_id:
                _LOGGER.debug("HA get_state entity_id=%s found state=%s", entity_id, entity.state)
                return entity.as_tool_result()
        _LOGGER.debug("HA get_state entity_id=%s not found", entity_id)
        return {"entity_id": entity_id, "found": False}

    async def call_service(self, domain: str, service: str, target: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
        payload = dict(target)
        payload.update(data)
        _LOGGER.debug("HA call_service domain=%s service=%s target=%s data=%s", domain, service, target, data)
        result = await self._request("POST", f"/api/services/{domain}/{service}", json_payload=payload)
        self._states_cache = None
        _LOGGER.debug("HA call_service raw_result_count=%s", len(result) if isinstance(result, list) else "n/a")
        return {
            "domain": domain,
            "service": service,
            "target": target,
            "data": data,
            "result": result,
        }

    async def _entity_records(self) -> list[EntityRecord]:
        states = await self._states()
        await self._ensure_registry_cache()

        records: list[EntityRecord] = []
        for state in states:
            entity_id = str(state["entity_id"])
            domain = entity_id.split(".", 1)[0]
            attributes = state.get("attributes") or {}
            records.append(
                EntityRecord(
                    entity_id=entity_id,
                    name=_entity_name(entity_id, attributes),
                    state=str(state.get("state", "unknown")),
                    domain=domain,
                    area=self._areas_by_id.get(self._entity_area_by_id.get(entity_id, "")),
                    attributes=attributes,
                )
            )
        return records

    async def _states(self) -> list[dict[str, Any]]:
        if self._states_cache is None:
            response = await self._request("GET", "/api/states")
            if not isinstance(response, list):
                raise ValueError("Unexpected Home Assistant states response")
            self._states_cache = response
        return self._states_cache

    async def _ensure_registry_cache(self) -> None:
        if self._areas_by_id and self._entity_area_by_id:
            return

        try:
            areas = await self._ws_command("config/area_registry/list")
            entity_registry = await self._ws_command("config/entity_registry/list")
            device_registry = await self._ws_command("config/device_registry/list")
        except Exception:
            self._areas_by_id = {}
            self._entity_area_by_id = {}
            return

        self._areas_by_id = {entry["area_id"]: entry["name"] for entry in areas if isinstance(entry, dict) and entry.get("area_id") and entry.get("name")}
        device_area_by_id = {entry["id"]: entry["area_id"] for entry in device_registry if isinstance(entry, dict) and entry.get("id") and entry.get("area_id")}

        entity_area_by_id: dict[str, str] = {}
        for entry in entity_registry:
            if not isinstance(entry, dict) or not entry.get("entity_id"):
                continue
            area_id = entry.get("area_id") or device_area_by_id.get(entry.get("device_id"))
            if area_id:
                entity_area_by_id[str(entry["entity_id"])] = str(area_id)
        self._entity_area_by_id = entity_area_by_id

    async def _request(self, method: str, path: str, json_payload: Optional[dict[str, Any]] = None) -> Any:
        session = await self._session_or_create()
        _LOGGER.debug("HA request method=%s path=%s payload=%s", method, path, json_payload)
        async with session.request(method, urljoin(self._base_url, path), json=json_payload, ssl=self._ssl_context()) as response:
            response.raise_for_status()
            result = await response.json()
            _LOGGER.debug("HA response method=%s path=%s status=%s", method, path, response.status)
            return result

    async def _ws_command(self, command_type: str) -> Any:
        session = await self._session_or_create()
        ssl_context = self._ssl_context()
        websocket_url = self._base_url.replace("http://", "ws://").replace("https://", "wss://") + "/api/websocket"
        _LOGGER.debug("HA websocket command=%s", command_type)
        async with session.ws_connect(websocket_url, ssl=ssl_context) as websocket:
            await websocket.receive_json()
            await websocket.send_json({"type": "auth", "access_token": self._token})
            auth_response = await websocket.receive_json()
            if auth_response.get("type") != "auth_ok":
                raise RuntimeError("Home Assistant websocket authentication failed")

            await websocket.send_json({"id": 1, "type": command_type})
            while True:
                message = await websocket.receive_json()
                if message.get("id") == 1:
                    if not message.get("success", False):
                        raise RuntimeError(f"Home Assistant websocket command failed: {command_type}")
                    _LOGGER.debug("HA websocket command=%s success", command_type)
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

    def _ssl_context(self) -> bool | ssl.SSLContext:
        if self._verify_ssl:
            return True
        return False


def _entity_name(entity_id: str, attributes: dict[str, Any]) -> str:
    return str(attributes.get("friendly_name") or attributes.get("name") or entity_id)


def _matches_query(entity: EntityRecord, query: str) -> bool:
    query_tokens = _query_match_tokens(query)
    entity_tokens = _entity_search_tokens(entity)
    return all(any(candidate in entity_tokens for candidate in token_candidates) for token_candidates in query_tokens)


def _entity_match_score(entity: EntityRecord, query: str) -> int:
    if not query:
        return 0

    query_tokens = _query_match_tokens(query)
    entity_tokens = _entity_search_tokens(entity)
    name_tokens = _normalized_tokens(entity.name)
    area_tokens = _normalized_tokens(entity.area or "")

    score = 0
    for token_candidates in query_tokens:
        if any(token in name_tokens for token in token_candidates):
            score += 3
        elif any(token in area_tokens for token in token_candidates):
            score += 2
        elif any(token in entity_tokens for token in token_candidates):
            score += 1
    return score


def _entity_search_tokens(entity: EntityRecord) -> set[str]:
    return _normalized_tokens(
        " ".join(
            [
                entity.entity_id,
                entity.name,
                entity.area or "",
                entity.domain,
                str(entity.attributes.get("friendly_name") or ""),
            ]
        )
    )


def _matches_area(area_name: Optional[str], area_query: str) -> bool:
    if not area_name:
        return False
    return _normalized_tokens(area_query).issubset(_normalized_tokens(area_name))


def _normalized_tokens(value: str) -> set[str]:
    cleaned = value.lower().replace("_", " ").replace(".", " ").replace("-", " ")
    raw_tokens = [token for token in cleaned.split() if token and token not in _STOP_TOKENS]
    expanded: set[str] = set()
    for token in raw_tokens:
        expanded.update(_token_candidates(token))
    return expanded


def _query_match_tokens(value: str) -> list[set[str]]:
    cleaned = value.lower().replace("_", " ").replace(".", " ").replace("-", " ")
    raw_tokens = [token for token in cleaned.split() if token and token not in _STOP_TOKENS]
    return [_token_candidates(token) for token in raw_tokens]


def _token_candidates(token: str) -> set[str]:
    candidates = {token, _singularize(token)}
    if token == "lights":
        candidates.add("light")
    elif token == "switches":
        candidates.add("switch")
    return {candidate for candidate in candidates if candidate}


def _singularize(token: str) -> str:
    if token.endswith("ies") and len(token) > 3:
        return token[:-3] + "y"
    if token.endswith(("ches", "shes", "xes", "zes", "sses")) and len(token) > 4:
        return token[:-2]
    if token.endswith("s") and len(token) > 3:
        return token[:-1]
    return token


def _summarize_tool_result(result: dict[str, Any]) -> dict[str, Any]:
    if "entities" in result:
        return {
            "count": result.get("count", 0),
            "entity_ids": [entity.get("entity_id") for entity in result.get("entities", [])[:5]],
        }
    if "entity_id" in result and "state" in result:
        return {"entity_id": result.get("entity_id"), "state": result.get("state")}
    if "domain" in result and "service" in result:
        return {
            "domain": result.get("domain"),
            "service": result.get("service"),
            "target": result.get("target"),
            "result_count": len(result.get("result", [])) if isinstance(result.get("result"), list) else 0,
        }
    return result


_STOP_TOKENS = {
    "a",
    "an",
    "the",
    "please",
    "my",
    "all",
    "only",
    "of",
    "in",
    "on",
    "to",
    "for",
}


def _suggested_services(domain: str) -> list[str]:
    suggestions = {
        "light": ["turn_on", "turn_off", "toggle"],
        "switch": ["turn_on", "turn_off", "toggle"],
        "input_boolean": ["turn_on", "turn_off", "toggle"],
        "scene": ["turn_on"],
        "script": ["turn_on"],
        "automation": ["turn_on", "turn_off", "trigger"],
        "climate": ["turn_on", "turn_off", "set_temperature", "set_hvac_mode"],
        "media_player": ["turn_on", "turn_off", "media_play", "media_pause", "play_media"],
        "fan": ["turn_on", "turn_off", "set_percentage"],
        "cover": ["open_cover", "close_cover", "stop_cover"],
        "vacuum": ["start", "stop", "return_to_base"],
        "lock": ["lock", "unlock"],
    }
    return suggestions.get(domain, [])


def _curated_attributes(attributes: dict[str, Any]) -> dict[str, Any]:
    allowed = [
        "friendly_name",
        "unit_of_measurement",
        "device_class",
        "temperature",
        "current_temperature",
        "hvac_mode",
        "brightness",
        "color_mode",
        "supported_color_modes",
        "fan_mode",
        "preset_mode",
        "humidity",
    ]
    return {key: value for key, value in attributes.items() if key in allowed}
