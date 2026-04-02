"""Config flow for Realtime Satellite."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.helpers import selector

from .const import DEFAULT_SETTINGS, DOMAIN


def _options_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional("openai_api_key", default=defaults.get("openai_api_key", "")): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
            vol.Optional("openai_model", default=defaults["openai_model"]): selector.SelectSelector(
                selector.SelectSelectorConfig(options=defaults["openai_model_options"], mode=selector.SelectSelectorMode.DROPDOWN)
            ),
            vol.Optional("openai_voice", default=defaults["openai_voice"]): selector.SelectSelector(
                selector.SelectSelectorConfig(options=defaults["openai_voice_options"], mode=selector.SelectSelectorMode.DROPDOWN)
            ),
            vol.Optional("openai_instructions", default=defaults["openai_instructions"]): selector.TextSelector(selector.TextSelectorConfig(multiline=True)),
            vol.Optional("wakeup_sound", default=defaults["wakeup_sound"]): selector.TextSelector(),
            vol.Optional("processing_sound", default=defaults["processing_sound"]): selector.TextSelector(),
            vol.Optional("tool_call_sound", default=defaults["tool_call_sound"]): selector.TextSelector(),
            vol.Optional("session_end_sound", default=defaults["session_end_sound"]): selector.TextSelector(),
            vol.Optional("session_timeout_seconds", default=defaults["session_timeout_seconds"]): selector.NumberSelector(selector.NumberSelectorConfig(min=1, max=120, step=1, mode=selector.NumberSelectorMode.BOX)),
            vol.Optional("vad_threshold", default=defaults["vad_threshold"]): selector.NumberSelector(selector.NumberSelectorConfig(min=0.001, max=0.1, step=0.001, mode=selector.NumberSelectorMode.BOX)),
            vol.Optional("min_speech_seconds", default=defaults["min_speech_seconds"]): selector.NumberSelector(selector.NumberSelectorConfig(min=0.1, max=5, step=0.1, mode=selector.NumberSelectorMode.BOX)),
            vol.Optional("end_silence_seconds", default=defaults["end_silence_seconds"]): selector.NumberSelector(selector.NumberSelectorConfig(min=0.1, max=5, step=0.1, mode=selector.NumberSelectorMode.BOX)),
            vol.Optional("refractory_seconds", default=defaults["refractory_seconds"]): selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=10, step=0.1, mode=selector.NumberSelectorMode.BOX)),
            vol.Optional("follow_up_after_tool_call", default=defaults["follow_up_after_tool_call"]): selector.BooleanSelector(),
            vol.Optional("enable_tool_get_entities", default=defaults["enable_tool_get_entities"]): selector.BooleanSelector(),
            vol.Optional("enable_tool_get_state", default=defaults["enable_tool_get_state"]): selector.BooleanSelector(),
            vol.Optional("enable_tool_call_service", default=defaults["enable_tool_call_service"]): selector.BooleanSelector(),
            vol.Optional("enable_tool_web_search", default=defaults["enable_tool_web_search"]): selector.BooleanSelector(),
        }
    )


class RealtimeSatelliteConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        return self.async_create_entry(title="Realtime Satellite", data={})

    async def async_step_import(self, import_config: dict[str, Any]) -> ConfigFlowResult:
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        return self.async_create_entry(title="Realtime Satellite", data={})

    @staticmethod
    def async_get_options_flow(config_entry):
        return RealtimeSatelliteOptionsFlow(config_entry)


class RealtimeSatelliteOptionsFlow(OptionsFlow):
    def __init__(self, config_entry) -> None:
        self._config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        manager = self.hass.data[DOMAIN][self._config_entry.entry_id]
        defaults = dict(DEFAULT_SETTINGS)
        defaults.update(manager.settings)
        defaults.update(manager.catalog)
        if user_input is not None:
            for key, value in user_input.items():
                await manager.async_update_setting(key, value)
            if "openai_api_key" in user_input:
                await manager.async_refresh_catalog()
            return self.async_create_entry(title="", data={})

        await manager.async_refresh_catalog()
        defaults.update(manager.catalog)
        return self.async_show_form(step_id="init", data_schema=_options_schema(defaults))
