"""Realtime Satellite Home Assistant integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers.event import async_track_time_interval
import voluptuous as vol

from .const import DEFAULT_SETTINGS, DOMAIN, PLATFORMS, SERVICE_APPLY_SETTINGS, SERVICE_RECORD_ACTIVITY, SERVICE_REFRESH_OPENAI_CATALOG, SERVICE_REFRESH_OPENAI_USAGE, USAGE_REFRESH_INTERVAL_MINUTES
from .manager import RealtimeSatelliteSettingsManager


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    hass.data.setdefault(DOMAIN, {})
    if DOMAIN in config:
        hass.async_create_task(hass.config_entries.flow.async_init(DOMAIN, context={"source": "import"}, data={}))
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    domain_data = hass.data.setdefault(DOMAIN, {})
    manager: RealtimeSatelliteSettingsManager | None = domain_data.get("manager")
    if manager is None:
        manager = RealtimeSatelliteSettingsManager(hass)
        await manager.async_load()
        domain_data["manager"] = manager

    domain_data[entry.entry_id] = manager
    await _register_services(hass, manager)
    if "usage_unsub" not in domain_data:
        @callback
        def _schedule_usage_refresh(now) -> None:
            del now
            hass.async_create_task(manager.async_refresh_usage())

        domain_data["usage_unsub"] = async_track_time_interval(hass, _schedule_usage_refresh, timedelta(minutes=USAGE_REFRESH_INTERVAL_MINUTES))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    domain_data = hass.data.get(DOMAIN, {})
    domain_data.pop(entry.entry_id, None)
    return unload_ok


async def _register_services(hass: HomeAssistant, manager: RealtimeSatelliteSettingsManager) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_APPLY_SETTINGS):
        return

    service_schema = vol.Schema({vol.Optional(key): object for key in DEFAULT_SETTINGS})

    async def async_apply_settings(call: ServiceCall) -> None:
        for key in DEFAULT_SETTINGS:
            if key in call.data:
                await manager.async_update_setting(key, call.data[key])
        if "openai_api_key" in call.data:
            await manager.async_refresh_catalog()
        if "openai_admin_api_key" in call.data:
            await manager.async_refresh_usage()

    hass.services.async_register(DOMAIN, SERVICE_APPLY_SETTINGS, async_apply_settings, schema=service_schema)

    async def async_refresh_catalog(call: ServiceCall) -> None:
        del call
        await manager.async_refresh_catalog()

    hass.services.async_register(DOMAIN, SERVICE_REFRESH_OPENAI_CATALOG, async_refresh_catalog)

    async def async_refresh_usage(call: ServiceCall) -> None:
        del call
        await manager.async_refresh_usage()

    hass.services.async_register(DOMAIN, SERVICE_REFRESH_OPENAI_USAGE, async_refresh_usage)

    activity_schema = vol.Schema(
        {
            vol.Required("category"): str,
            vol.Required("message"): str,
            vol.Optional("details", default={}): dict,
        }
    )

    async def async_record_activity(call: ServiceCall) -> None:
        await manager.async_record_activity(str(call.data["category"]), str(call.data["message"]), dict(call.data.get("details", {})))

    hass.services.async_register(DOMAIN, SERVICE_RECORD_ACTIVITY, async_record_activity, schema=activity_schema)
