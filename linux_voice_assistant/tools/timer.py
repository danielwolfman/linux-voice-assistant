"""Asynchronous voice-assistant timers exposed as Realtime tools."""

from __future__ import annotations

import asyncio
import math
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from ..ha_tools.client import HomeAssistantToolBridge

TimerCompletionCallback = Callable[["TimerRecord"], Awaitable[None]]

_MAX_TIMER_SECONDS = 7 * 24 * 60 * 60


@dataclass
class TimerRecord:
    id: str
    duration_seconds: int
    label: str
    origin_session_id: Optional[str]
    started_at: float = field(default_factory=time.time)
    status: str = "running"
    finished_at: Optional[float] = None
    cancelled_at: Optional[float] = None
    ha_entity_id: Optional[str] = None
    ha_status: Optional[dict[str, Any]] = None
    finished_sound: Optional[str] = None
    _task: Optional[asyncio.Task[None]] = field(default=None, repr=False, compare=False)

    @property
    def due_at(self) -> float:
        return self.started_at + self.duration_seconds

    @property
    def remaining_seconds(self) -> int:
        if self.status == "running":
            return max(0, int(math.ceil(self.due_at - time.time())))
        return 0

    def as_tool_result(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "label": self.label,
            "duration_seconds": self.duration_seconds,
            "remaining_seconds": self.remaining_seconds,
            "started_at": self.started_at,
            "due_at": self.due_at,
            "finished_at": self.finished_at,
            "cancelled_at": self.cancelled_at,
            "home_assistant_entity_id": self.ha_entity_id,
            "home_assistant": self.ha_status,
        }


class TimerManager:
    def __init__(
        self,
        *,
        completion_callback: Optional[TimerCompletionCallback] = None,
        finished_sound: Optional[str] = None,
    ) -> None:
        self._completion_callback = completion_callback
        self._finished_sound = finished_sound
        self._timers: dict[str, TimerRecord] = {}

    def active_timers(self) -> list[TimerRecord]:
        return [timer for timer in self._timers.values() if timer.status == "running"]

    async def close(self) -> None:
        for timer in self.active_timers():
            timer.status = "cancelled"
            timer.cancelled_at = time.time()
            if timer._task is not None:
                timer._task.cancel()
        await asyncio.sleep(0)

    async def start_timer(
        self,
        *,
        duration_seconds: int,
        label: str,
        origin_session_id: Optional[str],
        ha_entity_id: Optional[str] = None,
        ha_status: Optional[dict[str, Any]] = None,
    ) -> TimerRecord:
        timer = TimerRecord(
            id=time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8],
            duration_seconds=duration_seconds,
            label=label.strip() or "timer",
            origin_session_id=origin_session_id,
            ha_entity_id=ha_entity_id,
            ha_status=ha_status,
            finished_sound=self._finished_sound,
        )
        timer._task = asyncio.create_task(self._run_timer(timer))
        self._timers[timer.id] = timer
        return timer

    def get_timers(self, *, include_finished: bool = False) -> list[TimerRecord]:
        timers = list(self._timers.values())
        if not include_finished:
            timers = [timer for timer in timers if timer.status == "running"]
        timers.sort(key=lambda timer: timer.due_at)
        return timers

    async def cancel_timer(self, timer_id: str = "") -> Optional[TimerRecord]:
        timer = self._resolve_timer(timer_id)
        if timer is None:
            return None
        if timer.status != "running":
            return timer
        timer.status = "cancelled"
        timer.cancelled_at = time.time()
        if timer._task is not None:
            timer._task.cancel()
        return timer

    def _resolve_timer(self, timer_id: str) -> Optional[TimerRecord]:
        if timer_id:
            return self._timers.get(timer_id)
        running = self.active_timers()
        if not running:
            return None
        running.sort(key=lambda timer: timer.due_at)
        return running[0]

    async def _run_timer(self, timer: TimerRecord) -> None:
        try:
            await asyncio.sleep(timer.duration_seconds)
        except asyncio.CancelledError:
            return
        timer.status = "finished"
        timer.finished_at = time.time()
        if self._completion_callback is not None:
            await self._completion_callback(timer)


class TimerTool:
    def __init__(
        self,
        manager: TimerManager,
        origin_session_id: Optional[str],
        ha_tools: HomeAssistantToolBridge,
    ) -> None:
        self._manager = manager
        self._origin_session_id = origin_session_id
        self._ha_tools = ha_tools

    def tool_definitions(self) -> list[dict[str, Any]]:
        return [_start_timer_tool(), _get_timers_tool(), _cancel_timer_tool()]

    async def execute_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "start_timer":
            return await self._start_timer(arguments)
        if name == "get_timers":
            include_finished = bool(arguments.get("include_finished", False))
            return {"status": "ok", "timers": [timer.as_tool_result() for timer in self._manager.get_timers(include_finished=include_finished)]}
        if name == "cancel_timer":
            timer = await self._manager.cancel_timer(str(arguments.get("timer_id") or ""))
            if timer is None:
                return {"status": "not_found", "message": "No running timer was found."}
            await self._cancel_home_assistant_timer(timer)
            return {"status": "cancelled" if timer.status == "cancelled" else "not_running", "timer": timer.as_tool_result()}
        raise ValueError(f"Unsupported timer tool: {name}")

    async def close(self) -> None:
        return None

    async def _start_timer(self, arguments: dict[str, Any]) -> dict[str, Any]:
        duration_seconds = _coerce_duration_seconds(arguments)
        if duration_seconds is None:
            return {"status": "error", "error": "duration_seconds must be a positive number of seconds."}
        if duration_seconds > _MAX_TIMER_SECONDS:
            return {"status": "error", "error": "Timers are limited to seven days."}

        label = str(arguments.get("label") or "timer").strip() or "timer"
        requested_ha_entity_id = str(arguments.get("home_assistant_entity_id") or "").strip()
        ha_entity_id, ha_status = await self._start_home_assistant_timer(duration_seconds, requested_ha_entity_id)
        timer = await self._manager.start_timer(
            duration_seconds=duration_seconds,
            label=label,
            origin_session_id=self._origin_session_id,
            ha_entity_id=ha_entity_id,
            ha_status=ha_status,
        )
        return {
            "status": "accepted",
            "timer": timer.as_tool_result(),
            "message": "Timer started. The assistant will notify this device when it finishes.",
        }

    async def _start_home_assistant_timer(self, duration_seconds: int, requested_entity_id: str) -> tuple[Optional[str], dict[str, Any]]:
        entity_id = requested_entity_id
        selection_reason = ""
        if not entity_id:
            entity_id, selection_reason = await self._select_home_assistant_timer_entity()
        if not entity_id:
            return None, {"status": "not_used", "reason": selection_reason or "No Home Assistant timer entity was specified or found."}

        try:
            result = await self._ha_tools.call_service(
                domain="timer",
                service="start",
                target={"entity_id": entity_id},
                data={"duration": _format_duration(duration_seconds)},
            )
            return entity_id, {"status": "started", "entity_id": entity_id, "result": result}
        except Exception as err:  # pylint: disable=broad-except
            return entity_id, {"status": "failed", "entity_id": entity_id, "error": str(err)}

    async def _cancel_home_assistant_timer(self, timer: TimerRecord) -> None:
        if not timer.ha_entity_id:
            return
        try:
            await self._ha_tools.call_service(
                domain="timer",
                service="cancel",
                target={"entity_id": timer.ha_entity_id},
                data={},
            )
        except Exception:
            return

    async def _select_home_assistant_timer_entity(self) -> tuple[Optional[str], str]:
        try:
            result = await self._ha_tools.get_entities(domain="timer", limit=25)
        except Exception as err:  # pylint: disable=broad-except
            return None, f"Home Assistant timer lookup failed: {err}"

        entities = result.get("entities") if isinstance(result, dict) else None
        if not isinstance(entities, list) or not entities:
            return None, "No Home Assistant timer entities were found."

        idle_entities = [entity for entity in entities if isinstance(entity, dict) and str(entity.get("state") or "").lower() in {"idle", "paused", "unknown"}]
        candidates = idle_entities or [entity for entity in entities if isinstance(entity, dict)]
        preferred = [entity for entity in candidates if _looks_like_voice_timer_entity(entity)]
        if len(preferred) == 1:
            return str(preferred[0]["entity_id"]), "Selected the voice-assistant Home Assistant timer entity."
        if len(candidates) == 1:
            return str(candidates[0]["entity_id"]), "Selected the only Home Assistant timer entity."
        return None, "Multiple Home Assistant timer entities exist; using the backend timer only."


def format_timer_finished_notification(timer: TimerRecord) -> str:
    label = timer.label.strip() or "timer"
    if label.lower() == "timer":
        return "The timer is done."
    return f"The {label} timer is done."


def _coerce_duration_seconds(arguments: dict[str, Any]) -> Optional[int]:
    value = arguments.get("duration_seconds")
    if isinstance(value, bool) or value is None:
        return None
    try:
        duration = int(math.ceil(float(value)))
    except (TypeError, ValueError):
        return None
    return duration if duration > 0 else None


def _format_duration(duration_seconds: int) -> str:
    hours, remainder = divmod(duration_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _looks_like_voice_timer_entity(entity: dict[str, Any]) -> bool:
    text = " ".join(
        str(entity.get(key) or "")
        for key in ("entity_id", "name")
    ).lower()
    return any(keyword in text for keyword in ("voice", "assistant", "berta", "jarvis"))


def _start_timer_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "name": "start_timer",
        "description": (
            "Start an asynchronous timer for the user. Use this when the user asks to set a timer. "
            "The backend will notify the requesting voice device when the timer finishes. "
            "If a Home Assistant timer helper is specified or unambiguous, it will be started too."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "duration_seconds": {
                    "type": "number",
                    "minimum": 1,
                    "maximum": _MAX_TIMER_SECONDS,
                    "description": "Timer duration in seconds.",
                },
                "label": {
                    "type": "string",
                    "description": "Short timer label, such as pasta, laundry, or tea.",
                },
                "home_assistant_entity_id": {
                    "type": "string",
                    "description": "Optional exact Home Assistant timer entity_id to mirror, such as timer.voice_assistant.",
                },
            },
            "required": ["duration_seconds"],
            "additionalProperties": False,
        },
    }


def _get_timers_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "name": "get_timers",
        "description": "List running timers and their remaining time. Use for questions like how long is left on my timer.",
        "parameters": {
            "type": "object",
            "properties": {
                "include_finished": {
                    "type": "boolean",
                    "default": False,
                    "description": "Include finished and cancelled timers as well as running timers.",
                },
            },
            "additionalProperties": False,
        },
    }


def _cancel_timer_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "name": "cancel_timer",
        "description": "Cancel a running timer. If timer_id is omitted, cancel the timer that will finish next.",
        "parameters": {
            "type": "object",
            "properties": {
                "timer_id": {
                    "type": "string",
                    "description": "Optional timer id. Omit to cancel the next running timer.",
                },
            },
            "additionalProperties": False,
        },
    }
