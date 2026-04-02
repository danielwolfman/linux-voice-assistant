"""Number entities for Realtime Satellite settings."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, NUMBER_SETTINGS
from .entity import RealtimeSatelliteEntity
from .manager import RealtimeSatelliteSettingsManager


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    manager: RealtimeSatelliteSettingsManager = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([RealtimeSatelliteNumberEntity(manager, key, definition) for key, definition in NUMBER_SETTINGS.items()])


class RealtimeSatelliteNumberEntity(RealtimeSatelliteEntity, NumberEntity):
    _attr_mode = NumberMode.BOX

    def __init__(self, manager: RealtimeSatelliteSettingsManager, key: str, definition: dict[str, object]) -> None:
        super().__init__(manager)
        self.key = key
        self._attr_entity_category = EntityCategory.CONFIG
        self._attr_name = str(definition["name"])
        self._attr_unique_id = f"realtime_satellite_{key}"
        self._attr_native_min_value = float(definition["min"])
        self._attr_native_max_value = float(definition["max"])
        self._attr_native_step = float(definition["step"])
        self._attr_native_unit_of_measurement = definition["unit"]
        self._attr_icon = "mdi:tune"

    @property
    def native_value(self) -> float:
        return float(self.manager.settings.get(self.key, 0.0))

    async def async_set_native_value(self, value: float) -> None:
        await self.manager.async_update_setting(self.key, float(value))
