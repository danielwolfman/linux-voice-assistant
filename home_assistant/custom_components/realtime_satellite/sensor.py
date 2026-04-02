"""Sensor platform for Realtime Satellite settings."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SETTINGS_ENTITY_NAME
from .entity import RealtimeSatelliteEntity
from .manager import RealtimeSatelliteSettingsManager


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    manager: RealtimeSatelliteSettingsManager = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([RealtimeSatelliteSettingsSensor(manager)])


class RealtimeSatelliteSettingsSensor(RealtimeSatelliteEntity, SensorEntity):
    _attr_name = SETTINGS_ENTITY_NAME
    _attr_unique_id = "realtime_satellite_settings"
    _attr_icon = "mdi:tune-variant"

    @property
    def native_value(self) -> int:
        return self.manager.revision

    @property
    def extra_state_attributes(self):
        return self.manager.sensor_attributes()
