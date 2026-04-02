"""Settings storage for the Realtime Satellite integration."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store

from .catalog import fetch_openai_catalog, load_openai_api_key
from .const import DEFAULT_OPENAI_MODEL_OPTIONS, DEFAULT_OPENAI_VOICE_OPTIONS, DEFAULT_SETTINGS, DOMAIN, PRIVATE_SETTINGS, SETTINGS_ENTITY_MARKER, STORAGE_KEY, STORAGE_VERSION, UPDATE_SIGNAL


class RealtimeSatelliteSettingsManager:
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self.settings: dict[str, Any] = dict(DEFAULT_SETTINGS)
        self.catalog: dict[str, list[str]] = {
            "openai_model_options": list(DEFAULT_OPENAI_MODEL_OPTIONS),
            "openai_voice_options": list(DEFAULT_OPENAI_VOICE_OPTIONS),
        }
        self.revision = 0

    async def async_load(self) -> None:
        stored = await self._store.async_load()
        if isinstance(stored, dict):
            self.settings.update(stored)
        await self.async_refresh_catalog()

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

    @callback
    def sensor_attributes(self) -> dict[str, Any]:
        public_settings = {key: value for key, value in self.settings.items() if key not in PRIVATE_SETTINGS}
        return {
            **public_settings,
            **self.catalog,
            "integration_domain": DOMAIN,
            SETTINGS_ENTITY_MARKER: True,
            "revision": self.revision,
        }
