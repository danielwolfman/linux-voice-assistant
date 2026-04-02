"""Switch entities for Realtime Satellite settings."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SWITCH_SETTINGS
from .entity import RealtimeSatelliteEntity
from .manager import RealtimeSatelliteSettingsManager


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    manager: RealtimeSatelliteSettingsManager = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([RealtimeSatelliteSwitchEntity(manager, key, definition) for key, definition in SWITCH_SETTINGS.items()])


class RealtimeSatelliteSwitchEntity(RealtimeSatelliteEntity, SwitchEntity):
    def __init__(self, manager: RealtimeSatelliteSettingsManager, key: str, definition: dict[str, object]) -> None:
        super().__init__(manager)
        self.key = key
        self._attr_entity_category = EntityCategory.CONFIG
        self._attr_name = str(definition["name"])
        self._attr_unique_id = f"openai_real_time_assistant_{key}"
        self._attr_icon = "mdi:toggle-switch"

    @property
    def is_on(self) -> bool:
        return bool(self.manager.settings.get(self.key, False))

    async def async_turn_on(self, **kwargs) -> None:
        await self.manager.async_update_setting(self.key, True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.manager.async_update_setting(self.key, False)
