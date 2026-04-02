"""Base entities for the Realtime Satellite integration."""

from __future__ import annotations

from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo, Entity

from .const import DOMAIN, UPDATE_SIGNAL
from .manager import RealtimeSatelliteSettingsManager


class RealtimeSatelliteEntity(Entity):
    _attr_has_entity_name = True

    def __init__(self, manager: RealtimeSatelliteSettingsManager) -> None:
        self.manager = manager
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, "settings")},
            name="OpenAI Real Time Assistant",
            manufacturer="OpenAI / Home Assistant",
            model="Linux OpenAI Real Time Assistant",
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, UPDATE_SIGNAL, self._handle_manager_update))

    def _handle_manager_update(self) -> None:
        self.schedule_update_ha_state(force_refresh=False)
