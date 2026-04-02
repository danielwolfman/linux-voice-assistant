"""Constants for the Realtime Satellite custom component."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "realtime_satellite"
UPDATE_SIGNAL = f"{DOMAIN}_updated"
STORAGE_KEY = f"{DOMAIN}.settings"
STORAGE_VERSION = 1
SETTINGS_ENTITY_NAME = "Realtime Satellite Settings"
SETTINGS_ENTITY_MARKER = "settings_entity"
SERVICE_APPLY_SETTINGS = "apply_settings"
SERVICE_REFRESH_OPENAI_CATALOG = "refresh_openai_catalog"

PLATFORMS = [Platform.SENSOR, Platform.TEXT, Platform.NUMBER, Platform.SWITCH, Platform.SELECT]

DEFAULT_OPENAI_MODEL_OPTIONS = ["gpt-realtime", "gpt-4o-realtime-preview", "gpt-realtime-mini"]
DEFAULT_OPENAI_VOICE_OPTIONS = ["alloy", "ash", "ballad", "cedar", "coral", "echo", "marin", "sage", "shimmer", "verse"]

DEFAULT_SETTINGS: dict[str, object] = {
    "openai_api_key": "",
    "openai_model": "gpt-realtime",
    "openai_voice": "marin",
    "openai_instructions": (
        "You are a Linux voice satellite for Home Assistant. "
        "Keep spoken replies short and use Home Assistant tools for smart-home state and control."
    ),
    "wakeup_sound": "sounds/wake_word_triggered.flac",
    "processing_sound": "sounds/processing.wav",
    "tool_call_sound": "sounds/tool_call_processing.wav",
    "session_end_sound": "sounds/mute_switch_on.flac",
    "session_timeout_seconds": 20.0,
    "vad_threshold": 0.005,
    "min_speech_seconds": 0.2,
    "end_silence_seconds": 0.8,
    "refractory_seconds": 2.0,
    "follow_up_after_tool_call": False,
    "enable_tool_get_entities": True,
    "enable_tool_get_state": True,
    "enable_tool_call_service": True,
    "enable_tool_web_search": True,
}

TEXT_SETTINGS: dict[str, dict[str, object]] = {
    "wakeup_sound": {"name": "Wakeup Sound", "max": 255},
    "processing_sound": {"name": "Processing Sound", "max": 255},
    "tool_call_sound": {"name": "Tool Call Sound", "max": 255},
    "session_end_sound": {"name": "Session End Sound", "max": 255},
}

SELECT_SETTINGS: dict[str, dict[str, object]] = {
    "openai_model": {"name": "OpenAI Model", "options_key": "openai_model_options", "fallback": DEFAULT_OPENAI_MODEL_OPTIONS},
    "openai_voice": {"name": "OpenAI Voice", "options_key": "openai_voice_options", "fallback": DEFAULT_OPENAI_VOICE_OPTIONS},
}

PRIVATE_SETTINGS = {"openai_api_key"}

NUMBER_SETTINGS: dict[str, dict[str, object]] = {
    "session_timeout_seconds": {"name": "Session Timeout", "min": 1.0, "max": 120.0, "step": 1.0, "unit": "s"},
    "vad_threshold": {"name": "VAD Threshold", "min": 0.001, "max": 0.1, "step": 0.001, "unit": None},
    "min_speech_seconds": {"name": "Minimum Speech Duration", "min": 0.1, "max": 5.0, "step": 0.1, "unit": "s"},
    "end_silence_seconds": {"name": "End Silence Duration", "min": 0.1, "max": 5.0, "step": 0.1, "unit": "s"},
    "refractory_seconds": {"name": "Wake Word Refractory", "min": 0.0, "max": 10.0, "step": 0.1, "unit": "s"},
}

SWITCH_SETTINGS: dict[str, dict[str, object]] = {
    "follow_up_after_tool_call": {"name": "Keep Listening After Tool Call"},
    "enable_tool_get_entities": {"name": "Enable Get Entities Tool"},
    "enable_tool_get_state": {"name": "Enable Get State Tool"},
    "enable_tool_call_service": {"name": "Enable Call Service Tool"},
    "enable_tool_web_search": {"name": "Enable Web Search Tool"},
}
