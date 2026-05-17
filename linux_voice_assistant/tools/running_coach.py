"""Codex-backed running coach dispatch for Health Auto Export events."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote

from aiohttp import ClientSession, web

from .codex_agent import CodexJobManager
from .discord_bridge import discord_channel_origin_session_id

_LOGGER = logging.getLogger(__name__)

DEFAULT_TALLY_COACH_CHANNEL_ID = "1504773998330773646"
DEFAULT_DISPATCH_DELAY_SECONDS = 90.0
RUN_DISTANCE_ENTITY = "hae.newautomation_walking_running_distance"
HEART_RATE_AVG_ENTITY = "hae.newautomation_heart_rate_avg"
HEART_RATE_MAX_ENTITY = "hae.newautomation_heart_rate_max"
WALKING_SPEED_ENTITY = "hae.newautomation_walking_speed"
ACTIVE_ENERGY_ENTITY = "hae.newautomation_active_energy"
STEP_COUNT_ENTITY = "hae.newautomation_step_count"
RUN_CLUSTER_GAP_SECONDS = 1800.0
RUN_CONTEXT_WINDOW_SECONDS = 600.0

_HISTORY_ENTITIES = [
    RUN_DISTANCE_ENTITY,
    HEART_RATE_AVG_ENTITY,
    HEART_RATE_MAX_ENTITY,
    WALKING_SPEED_ENTITY,
    ACTIVE_ENERGY_ENTITY,
    STEP_COUNT_ENTITY,
]


@dataclass(frozen=True)
class StateSample:
    entity_id: str
    ts: float
    state: float


class RunningCoachService:
    def __init__(
        self,
        *,
        codex_manager: CodexJobManager,
        ha_url: str,
        ha_token: str,
        ha_verify_ssl: bool,
    ) -> None:
        self._codex_manager = codex_manager
        self._ha_url = ha_url.rstrip("/")
        self._ha_token = ha_token
        self._ha_verify_ssl = ha_verify_ssl
        self._tasks: set[asyncio.Task[None]] = set()

    def register_routes(self, app: web.Application, *, path: str = "/coach/tally/run") -> None:
        app.router.add_post(path, self.handle_tally_run)

    async def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def handle_tally_run(self, request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"status": "error", "error": "Request body must be JSON."}, status=400)
        if not isinstance(payload, dict):
            return web.json_response({"status": "error", "error": "Request body must be a JSON object."}, status=400)

        delay_seconds = _coerce_float(payload.get("delay_seconds"), DEFAULT_DISPATCH_DELAY_SECONDS)
        task = asyncio.create_task(self._dispatch_tally_run_after_delay(payload, max(0.0, delay_seconds)))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return web.json_response({"status": "accepted", "delay_seconds": delay_seconds})

    async def _dispatch_tally_run_after_delay(self, payload: dict[str, Any], delay_seconds: float) -> None:
        try:
            if delay_seconds:
                await asyncio.sleep(delay_seconds)
            history = await fetch_health_history(
                ha_url=self._ha_url,
                ha_token=self._ha_token,
                verify_ssl=self._ha_verify_ssl,
                days=14,
            )
            summary = summarize_running_history(history, now_ts=time.time())
            task = build_tally_running_coach_task(payload, summary)
            channel_id = str(payload.get("channel_id") or DEFAULT_TALLY_COACH_CHANNEL_ID)
            result = await self._codex_manager.start_task(
                {
                    "task": task,
                    "execution_mode": "docker",
                    "origin_language": "he",
                },
                origin_session_id=discord_channel_origin_session_id(channel_id),
                allow_parallel=True,
            )
            if result.get("status") != "accepted":
                _LOGGER.warning("Failed to dispatch Tally running coach Codex task: %s", result)
        except asyncio.CancelledError:
            raise
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Failed to dispatch Tally running coach Codex task")


async def fetch_health_history(*, ha_url: str, ha_token: str, verify_ssl: bool, days: int = 14) -> dict[str, list[StateSample]]:
    start = (datetime.now().astimezone() - timedelta(days=days)).isoformat()
    entities = ",".join(_HISTORY_ENTITIES)
    url = f"{ha_url.rstrip('/')}/api/history/period/{quote(start)}?filter_entity_id={quote(entities)}"
    headers = {"Authorization": f"Bearer {ha_token}", "Accept": "application/json"}
    async with ClientSession(headers=headers) as session:
        async with session.get(url, ssl=verify_ssl, timeout=30) as response:
            response.raise_for_status()
            raw_history = await response.json()
    return parse_history_response(raw_history)


def parse_history_response(raw_history: Any) -> dict[str, list[StateSample]]:
    samples: dict[str, list[StateSample]] = {entity_id: [] for entity_id in _HISTORY_ENTITIES}
    if not isinstance(raw_history, list):
        return samples
    for entity_history in raw_history:
        if not isinstance(entity_history, list):
            continue
        for item in entity_history:
            if not isinstance(item, dict):
                continue
            entity_id = str(item.get("entity_id") or "")
            if entity_id not in samples:
                continue
            value = _coerce_float(item.get("state"))
            if value is None:
                continue
            ts = _state_timestamp(item)
            if ts is None:
                continue
            samples[entity_id].append(StateSample(entity_id=entity_id, ts=ts, state=value))
    for entity_samples in samples.values():
        entity_samples.sort(key=lambda sample: sample.ts)
    return samples


def summarize_running_history(samples: dict[str, list[StateSample]], *, now_ts: float) -> dict[str, Any]:
    distance_samples = samples.get(RUN_DISTANCE_ENTITY, [])
    runs = []
    current_group: list[dict[str, Any]] = []
    previous_ts = 0.0

    for distance in distance_samples:
        if distance.state <= 1:
            continue
        hr_avg = _nearest_sample(samples.get(HEART_RATE_AVG_ENTITY, []), distance.ts)
        hr_max = _nearest_sample(samples.get(HEART_RATE_MAX_ENTITY, []), distance.ts)
        if hr_avg is None or hr_max is None or hr_max.state < 120:
            continue
        point = {
            "ts": distance.ts,
            "distance_km": round(distance.state, 2),
            "heart_rate_avg_bpm": round(hr_avg.state, 1),
            "heart_rate_max_bpm": round(hr_max.state, 1),
            "speed_kmh": _average_near(samples.get(WALKING_SPEED_ENTITY, []), distance.ts),
            "active_energy_kj": _nearest_value(samples.get(ACTIVE_ENERGY_ENTITY, []), distance.ts),
            "steps": _nearest_value(samples.get(STEP_COUNT_ENTITY, []), distance.ts),
        }
        if current_group and distance.ts - previous_ts > RUN_CLUSTER_GAP_SECONDS:
            runs.append(_summarize_run_group(current_group))
            current_group = []
        current_group.append(point)
        previous_ts = distance.ts
    if current_group:
        runs.append(_summarize_run_group(current_group))

    today = datetime.fromtimestamp(now_ts).astimezone().date()
    seven_days_ago = now_ts - 7 * 86400
    today_runs = [run for run in runs if datetime.fromtimestamp(run["ended_at_ts"]).astimezone().date() == today]
    seven_day_runs = [run for run in runs if run["ended_at_ts"] >= seven_days_ago]
    latest_run = runs[-1] if runs else None
    previous_runs = runs[:-1] if latest_run else runs
    previous_best_distance = max((run["distance_km"] for run in previous_runs), default=None)
    previous_latest = previous_runs[-1] if previous_runs else None

    return {
        "latest_run": latest_run,
        "today": {
            "date": today.isoformat(),
            "run_count": len(today_runs),
            "total_km": round(sum(run["distance_km"] for run in today_runs), 2),
        },
        "last_7_days": {
            "run_count": len(seven_day_runs),
            "total_km": round(sum(run["distance_km"] for run in seven_day_runs), 2),
            "best_distance_km": max((run["distance_km"] for run in seven_day_runs), default=None),
        },
        "previous_best_distance_km": previous_best_distance,
        "previous_run": previous_latest,
        "recent_runs": runs[-6:],
    }


def build_tally_running_coach_task(payload: dict[str, Any], summary: dict[str, Any]) -> str:
    data = {
        "trigger": payload,
        "summary": summary,
    }
    return (
        "You are writing a Discord coaching message for Tally (טלי) after a likely running workout from Apple Health / Health Auto Export.\n"
        "Write only the final message text in Hebrew. Do not include a title, bullet list, JSON, or explanation.\n"
        "Keep it positive, specific, and coach-like, 2-4 short sentences.\n"
        "Use the data to say what Tally achieved today. Mention concrete progress or improvement when the data supports it, such as distance, weekly consistency, best recent distance, pace, or heart-rate effort.\n"
        "Do not overstate unsupported improvements. If there is no clear improvement, praise the completed effort and consistency.\n"
        "Address her as טלי. Avoid sounding generic.\n\n"
        "Data:\n"
        f"{json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)}"
    )


def _summarize_run_group(points: list[dict[str, Any]]) -> dict[str, Any]:
    best_distance = max(points, key=lambda point: point["distance_km"])
    speed_values = [point["speed_kmh"] for point in points if point.get("speed_kmh")]
    avg_speed = round(sum(speed_values) / len(speed_values), 2) if speed_values else None
    avg_pace = round(60.0 / avg_speed, 2) if avg_speed and avg_speed > 0 else None
    avg_hr_values = [point["heart_rate_avg_bpm"] for point in points if point.get("heart_rate_avg_bpm")]
    return {
        "started_at": datetime.fromtimestamp(points[0]["ts"]).astimezone().isoformat(timespec="seconds"),
        "ended_at": datetime.fromtimestamp(points[-1]["ts"]).astimezone().isoformat(timespec="seconds"),
        "ended_at_ts": points[-1]["ts"],
        "distance_km": best_distance["distance_km"],
        "avg_pace_min_per_km": avg_pace,
        "avg_heart_rate_bpm": round(sum(avg_hr_values) / len(avg_hr_values), 1) if avg_hr_values else None,
        "max_heart_rate_bpm": max(point["heart_rate_max_bpm"] for point in points),
        "sample_count": len(points),
    }


def _nearest_sample(samples: list[StateSample], ts: float, *, max_delta: float = RUN_CONTEXT_WINDOW_SECONDS) -> StateSample | None:
    nearest = min(samples, key=lambda sample: abs(sample.ts - ts), default=None)
    if nearest is None or abs(nearest.ts - ts) > max_delta:
        return None
    return nearest


def _nearest_value(samples: list[StateSample], ts: float) -> float | None:
    sample = _nearest_sample(samples, ts)
    return round(sample.state, 2) if sample is not None else None


def _average_near(samples: list[StateSample], ts: float, *, max_delta: float = RUN_CONTEXT_WINDOW_SECONDS) -> float | None:
    values = [sample.state for sample in samples if abs(sample.ts - ts) <= max_delta and sample.state > 0]
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def _state_timestamp(item: dict[str, Any]) -> float | None:
    raw = str(item.get("last_updated") or item.get("last_changed") or "")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _coerce_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value in (None, "", "unknown", "unavailable"):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default
