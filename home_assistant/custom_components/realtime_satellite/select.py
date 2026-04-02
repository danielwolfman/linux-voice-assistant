"""Select entities for Realtime Satellite settings."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SELECT_SETTINGS
from .entity import RealtimeSatelliteEntity
from .manager import RealtimeSatelliteSettingsManager


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    manager: RealtimeSatelliteSettingsManager = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([RealtimeSatelliteSelectEntity(manager, key, definition) for key, definition in SELECT_SETTINGS.items()])


class RealtimeSatelliteSelectEntity(RealtimeSatelliteEntity, SelectEntity):
    def __init__(self, manager: RealtimeSatelliteSettingsManager, key: str, definition: dict[str, object]) -> None:
        super().__init__(manager)
        self.key = key
        self.definition = definition
        self._attr_entity_category = EntityCategory.CONFIG
        self._attr_name = str(definition["name"])
        self._attr_unique_id = f"realtime_satellite_{key}"
        self._attr_icon = "mdi:format-list-bulleted"

    @property
    def current_option(self) -> str:
        return str(self.manager.settings.get(self.key, ""))

    @property
    def options(self) -> list[str]:
        options_key = str(self.definition["options_key"])
        catalog_options = self.manager.catalog.get(options_key)
        if catalog_options:
            return list(catalog_options)
        return list(self.definition["fallback"])

    async def async_select_option(self, option: str) -> None:
        await self.manager.async_update_setting(self.key, option)
