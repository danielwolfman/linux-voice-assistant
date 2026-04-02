"""Realtime Satellite Home Assistant integration."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall

from .const import DEFAULT_SETTINGS, DOMAIN, PLATFORMS, SERVICE_APPLY_SETTINGS, SERVICE_REFRESH_OPENAI_CATALOG
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
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unload_ok


async def _register_services(hass: HomeAssistant, manager: RealtimeSatelliteSettingsManager) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_APPLY_SETTINGS):
        return

    async def async_apply_settings(call: ServiceCall) -> None:
        for key in DEFAULT_SETTINGS:
            if key in call.data:
                await manager.async_update_setting(key, call.data[key])
        if "openai_api_key" in call.data:
            await manager.async_refresh_catalog()

    hass.services.async_register(DOMAIN, SERVICE_APPLY_SETTINGS, async_apply_settings)

    async def async_refresh_catalog(call: ServiceCall) -> None:
        del call
        await manager.async_refresh_catalog()

    hass.services.async_register(DOMAIN, SERVICE_REFRESH_OPENAI_CATALOG, async_refresh_catalog)
