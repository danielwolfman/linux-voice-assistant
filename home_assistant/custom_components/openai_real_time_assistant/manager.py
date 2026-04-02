"""Settings storage for the Realtime Satellite integration."""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store

from .catalog import fetch_openai_catalog, load_openai_api_key
from .const import ACTIVITY_HISTORY_LIMIT, DEFAULT_OPENAI_MODEL_OPTIONS, DEFAULT_OPENAI_VOICE_OPTIONS, DEFAULT_SETTINGS, DOMAIN, HISTORY_STORAGE_KEY, PRIVATE_SETTINGS, SETTINGS_ENTITY_MARKER, STORAGE_KEY, STORAGE_VERSION, UPDATE_SIGNAL
from .usage_api import fetch_usage_summaries


class RealtimeSatelliteSettingsManager:
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._history_store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, HISTORY_STORAGE_KEY)
        self.settings: dict[str, Any] = dict(DEFAULT_SETTINGS)
        self.catalog: dict[str, list[str]] = {
            "openai_model_options": list(DEFAULT_OPENAI_MODEL_OPTIONS),
            "openai_voice_options": list(DEFAULT_OPENAI_VOICE_OPTIONS),
        }
        self.activities: list[dict[str, Any]] = []
        self.usage_summary_cache: dict[str, dict[str, float | int]] = {
            "usage_last_hour": {"count": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cost_usd": 0.0},
            "usage_last_24_hours": {"count": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cost_usd": 0.0},
        }
        self.revision = 0

    async def async_load(self) -> None:
        stored = await self._store.async_load()
        if isinstance(stored, dict):
            self.settings.update(stored)
        self.settings.pop("enable_activity_logging", None)
        history = await self._history_store.async_load()
        if isinstance(history, dict):
            self.activities = list(history.get("activities", []))
            cached_summary = history.get("usage_summary_cache")
            if isinstance(cached_summary, dict):
                self.usage_summary_cache.update(cached_summary)
        self._prune_activities()
        await self.async_refresh_catalog()
        await self.async_refresh_usage()

    async def async_refresh_catalog(self) -> None:
        stored_api_key = self.settings.get("openai_api_key")
        api_key = str(stored_api_key) if stored_api_key else None
        if not api_key:
            api_key = await self.hass.async_add_executor_job(load_openai_api_key, self.hass.config.config_dir)
        self.catalog = await fetch_openai_catalog(api_key)

    async def async_update_setting(self, key: str, value: Any) -> None:
        if self.settings.get(key) == value:
            return

        self.settings[key] = value
        self.revision += 1
        await self._store.async_save(self.settings)
        async_dispatcher_send(self.hass, UPDATE_SIGNAL)
        if key == "openai_admin_api_key":
            await self.async_refresh_usage()

    async def async_record_activity(self, category: str, message: str, details: dict[str, Any] | None = None) -> None:
        entry = {
            "timestamp": _utcnow_iso(),
            "category": category,
            "message": message,
            "details": details or {},
        }
        self.activities.append(entry)
        self.activities = self.activities[-ACTIVITY_HISTORY_LIMIT:]
        await self._save_history()
        async_dispatcher_send(self.hass, UPDATE_SIGNAL)

    async def async_refresh_usage(self) -> None:
        admin_key = str(self.settings.get("openai_admin_api_key") or "").strip()
        if not admin_key:
            return
        try:
            self.usage_summary_cache = await fetch_usage_summaries(admin_key)
        except Exception:
            return
        await self._save_history()
        async_dispatcher_send(self.hass, UPDATE_SIGNAL)

    async def _save_history(self) -> None:
        await self._history_store.async_save({"activities": self.activities, "usage_summary_cache": self.usage_summary_cache})

    def _prune_activities(self) -> None:
        self.activities = self.activities[-ACTIVITY_HISTORY_LIMIT:]

    @callback
    def recent_activities(self) -> list[dict[str, Any]]:
        return list(reversed(self.activities[-50:]))

    @callback
    def latest_activity_state(self) -> str:
        recent = self.recent_activities()
        if not recent:
            return "No activity yet"
        latest = recent[0]
        text = f"{latest['category']}: {latest['message']}"
        return text if len(text) <= 255 else text[:252] + "..."

    @callback
    def recent_activities_markdown(self, limit: int = 20) -> str:
        lines = []
        for entry in self.recent_activities()[:limit]:
            timestamp = str(entry.get("timestamp", ""))[11:19]
            category = str(entry.get("category", ""))
            message = str(entry.get("message", ""))
            lines.append(f"- `{timestamp}` **{category}**: {message}")
        return "\n".join(lines)

    @callback
    def usage_summary(self, hours: int) -> dict[str, float | int]:
        key = "usage_last_hour" if hours == 1 else "usage_last_24_hours"
        return dict(self.usage_summary_cache.get(key, {"count": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cost_usd": 0.0}))

    @callback
    def sensor_attributes(self) -> dict[str, Any]:
        public_settings = {key: value for key, value in self.settings.items() if key not in PRIVATE_SETTINGS}
        return {
            **public_settings,
            **self.catalog,
            "recent_activities": self.recent_activities(),
            "usage_last_hour": self.usage_summary(1),
            "usage_last_24_hours": self.usage_summary(24),
            "integration_domain": DOMAIN,
            SETTINGS_ENTITY_MARKER: True,
            "revision": self.revision,
        }


def _utcnow_iso() -> str:
    return datetime.now(tz=UTC).isoformat()
