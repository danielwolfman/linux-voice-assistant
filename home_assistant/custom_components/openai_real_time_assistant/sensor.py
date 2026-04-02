"""Sensor platform for Realtime Satellite settings and activity."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import RealtimeSatelliteEntity
from .manager import RealtimeSatelliteSettingsManager


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    manager: RealtimeSatelliteSettingsManager = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            RealtimeSatelliteUsageCostSensor(manager, hours=1),
            RealtimeSatelliteUsageCostSensor(manager, hours=24),
            RealtimeSatelliteUsageTokensSensor(manager, hours=1),
            RealtimeSatelliteUsageTokensSensor(manager, hours=24),
        ]
    )
class RealtimeSatelliteUsageCostSensor(RealtimeSatelliteEntity, SensorEntity):
    _attr_icon = "mdi:currency-usd"
    _attr_native_unit_of_measurement = "USD"

    def __init__(self, manager: RealtimeSatelliteSettingsManager, hours: int) -> None:
        super().__init__(manager)
        self.hours = hours
        self._attr_name = f"Cost Last {hours}h" if hours == 1 else "Cost Last 24h"
        self._attr_unique_id = f"openai_real_time_assistant_cost_{hours}h"

    @property
    def native_value(self) -> float:
        return round(float(self.manager.usage_summary(self.hours)["cost_usd"]), 1)

    @property
    def extra_state_attributes(self):
        return self.manager.usage_summary(self.hours)


class RealtimeSatelliteUsageTokensSensor(RealtimeSatelliteEntity, SensorEntity):
    _attr_icon = "mdi:counter"
    _attr_native_unit_of_measurement = "tokens"

    def __init__(self, manager: RealtimeSatelliteSettingsManager, hours: int) -> None:
        super().__init__(manager)
        self.hours = hours
        self._attr_name = f"Tokens Last {hours}h" if hours == 1 else "Tokens Last 24h"
        self._attr_unique_id = f"openai_real_time_assistant_tokens_{hours}h"

    @property
    def native_value(self) -> int:
        return int(self.manager.usage_summary(self.hours)["total_tokens"])

    @property
    def extra_state_attributes(self):
        return self.manager.usage_summary(self.hours)
