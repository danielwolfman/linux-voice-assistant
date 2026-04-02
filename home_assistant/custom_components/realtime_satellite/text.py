"""Text entities for Realtime Satellite settings."""

from __future__ import annotations

from homeassistant.components.text import TextEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, TEXT_SETTINGS
from .entity import RealtimeSatelliteEntity
from .manager import RealtimeSatelliteSettingsManager


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    manager: RealtimeSatelliteSettingsManager = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([RealtimeSatelliteTextEntity(manager, key, definition) for key, definition in TEXT_SETTINGS.items()])


class RealtimeSatelliteTextEntity(RealtimeSatelliteEntity, TextEntity):
    def __init__(self, manager: RealtimeSatelliteSettingsManager, key: str, definition: dict[str, object]) -> None:
        super().__init__(manager)
        self.key = key
        self._attr_entity_category = EntityCategory.CONFIG
        self._attr_name = str(definition["name"])
        self._attr_unique_id = f"realtime_satellite_{key}"
        self._attr_native_max = int(definition["max"])
        self._attr_icon = "mdi:form-textbox"

    @property
    def native_value(self) -> str:
        return str(self.manager.settings.get(self.key, ""))

    async def async_set_value(self, value: str) -> None:
        await self.manager.async_update_setting(self.key, value)
