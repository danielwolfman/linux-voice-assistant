"""HTTP ingress for asynchronous Codex task dispatch."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from aiohttp import web

from .codex_agent import CodexJobManager
from .discord_bridge import discord_channel_origin_session_id, discord_origin_session_id

_LOGGER = logging.getLogger(__name__)


class CodexTaskService:
    def __init__(self, *, codex_manager: CodexJobManager) -> None:
        self._codex_manager = codex_manager
        self._tasks: set[asyncio.Task[None]] = set()

    def register_routes(self, app: web.Application, *, path: str = "/codex/tasks") -> None:
        app.router.add_post(path, self.handle_create_task)

    async def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def handle_create_task(self, request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"status": "error", "error": "Request body must be JSON."}, status=400)
        if not isinstance(payload, dict):
            return web.json_response({"status": "error", "error": "Request body must be a JSON object."}, status=400)

        arguments, origin_session_id, allow_parallel, delay_seconds, error = _parse_create_task_payload(payload)
        if error:
            return web.json_response({"status": "error", "error": error}, status=400)

        if delay_seconds > 0:
            background_task = asyncio.create_task(self._dispatch_after_delay(arguments, origin_session_id, allow_parallel, delay_seconds))
            self._tasks.add(background_task)
            background_task.add_done_callback(self._tasks.discard)
            return web.json_response({"status": "scheduled", "delay_seconds": delay_seconds})

        result = await self._codex_manager.start_task(arguments, origin_session_id=origin_session_id, allow_parallel=allow_parallel)
        http_status = 202 if result.get("status") == "accepted" else 409 if result.get("status") == "busy" else 400
        return web.json_response(result, status=http_status)

    async def _dispatch_after_delay(self, arguments: dict[str, Any], origin_session_id: str | None, allow_parallel: bool, delay_seconds: float) -> None:
        try:
            await asyncio.sleep(delay_seconds)
            result = await self._codex_manager.start_task(arguments, origin_session_id=origin_session_id, allow_parallel=allow_parallel)
            if result.get("status") != "accepted":
                _LOGGER.warning("Delayed Codex task dispatch failed: %s", result)
        except asyncio.CancelledError:
            raise
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Delayed Codex task dispatch crashed")


def _parse_create_task_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], str | None, bool, float, str]:
    task = str(payload.get("task") or payload.get("prompt") or "").strip()
    if not task:
        return {}, None, False, 0.0, "A non-empty task is required."

    context = payload.get("context")
    if context not in (None, "", {}, []):
        task = _append_context(task, context)

    arguments: dict[str, Any] = {
        "task": task,
        "execution_mode": str(payload.get("execution_mode") or "docker").strip().lower(),
    }
    for key in ("workspace", "origin_language", "host_execution_confirmed"):
        if key in payload:
            arguments[key] = payload[key]

    origin_session_id, error = _origin_from_delivery(payload.get("delivery"))
    if error:
        return {}, None, False, 0.0, error

    allow_parallel = bool(payload.get("allow_parallel", True))
    delay_seconds = max(0.0, _coerce_float(payload.get("delay_seconds"), 0.0))
    return arguments, origin_session_id, allow_parallel, delay_seconds, ""


def _append_context(task: str, context: Any) -> str:
    if isinstance(context, str):
        context_text = context.strip()
    else:
        context_text = json.dumps(context, ensure_ascii=False, indent=2, sort_keys=True)
    if not context_text:
        return task
    return f"{task.rstrip()}\n\nContext:\n{context_text}"


def _origin_from_delivery(delivery: Any) -> tuple[str | None, str]:
    if delivery in (None, "", {}, []):
        return None, ""
    if not isinstance(delivery, dict):
        return None, "delivery must be an object when provided."
    delivery_type = str(delivery.get("type") or "").strip().lower()
    if delivery_type == "discord_channel":
        channel_id = str(delivery.get("channel_id") or "").strip()
        if not channel_id:
            return None, "delivery.channel_id is required for discord_channel delivery."
        return discord_channel_origin_session_id(channel_id), ""
    if delivery_type == "discord_user":
        user_id = str(delivery.get("user_id") or "").strip()
        if not user_id:
            return None, "delivery.user_id is required for discord_user delivery."
        return discord_origin_session_id(user_id), ""
    if delivery_type == "voice_session":
        session_id = str(delivery.get("session_id") or "").strip()
        if not session_id:
            return None, "delivery.session_id is required for voice_session delivery."
        return session_id, ""
    if delivery_type in {"none", "silent"}:
        return f"codex-task:{int(time.time() * 1000)}", ""
    return None, f"Unsupported delivery.type: {delivery_type or 'missing'}"


def is_silent_codex_task_origin(origin_session_id: str | None) -> bool:
    return bool(origin_session_id and origin_session_id.startswith("codex-task:"))


def _coerce_float(value: Any, default: float) -> float:
    try:
        if value in (None, "", "unknown", "unavailable"):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default
