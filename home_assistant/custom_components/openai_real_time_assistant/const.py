"""Constants for the Realtime Satellite custom component."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "openai_real_time_assistant"
UPDATE_SIGNAL = f"{DOMAIN}_updated"
STORAGE_KEY = f"{DOMAIN}.settings"
HISTORY_STORAGE_KEY = f"{DOMAIN}.history"
STORAGE_VERSION = 1
SETTINGS_ENTITY_NAME = "Settings"
SETTINGS_ENTITY_MARKER = "settings_entity"
SERVICE_APPLY_SETTINGS = "apply_settings"
SERVICE_REFRESH_OPENAI_CATALOG = "refresh_openai_catalog"
SERVICE_REFRESH_OPENAI_USAGE = "refresh_openai_usage"
SERVICE_RECORD_ACTIVITY = "record_activity"
SERVICE_RECORD_USAGE = "record_usage"

ACTIVITY_HISTORY_LIMIT = 200
USAGE_REFRESH_INTERVAL_MINUTES = 15

PLATFORMS = [Platform.SENSOR, Platform.TEXT, Platform.NUMBER, Platform.SWITCH, Platform.SELECT]

DEFAULT_OPENAI_MODEL_OPTIONS = ["gpt-realtime", "gpt-4o-realtime-preview", "gpt-realtime-mini"]
DEFAULT_OPENAI_VOICE_OPTIONS = ["alloy", "ash", "ballad", "cedar", "coral", "echo", "marin", "sage", "shimmer", "verse"]

DEFAULT_SETTINGS: dict[str, object] = {
    "openai_api_key": "",
    "openai_admin_api_key": "",
    "openai_model": "gpt-realtime",
    "openai_voice": "coral",
    "openai_instructions": (
        "You are Mycroft. "
        "You speak only in Hebrew or English, even if you heard a different language from the user. "
        "You can control and inspect the user's smart home through the provided Home Assistant tools. "
        "For any request about lights, switches, climate, scenes, scripts, sensors, rooms, areas, or device state, use the Home Assistant tools instead of guessing. "
        "Never say you cannot interact with the real world when a Home Assistant tool can help. "
        "For natural-language device requests, first search broadly with get_entities using query and domain. "
        "Put the room and device words into query, for example office light or bedroom AC. "
        "Use the area parameter only when you are confident it matches the exact Home Assistant area name. "
        "If a search returns no matches, retry with alternate wording or a likely Home Assistant naming language. "
        "Do not keep searching indefinitely: after two failed searches, do at most one likely English or Home Assistant naming retry, then ask a short follow-up. "
        "When get_entities returns a plausible actionable entity, stop searching and use its suggested_service_domain and suggested_services to choose a call_service action. "
        "If a script or scene clearly matches the request, prefer calling it rather than searching for a lower-level player or device. "
        "For questions about whether music is playing in the salon/living room or what is currently playing there, do not start playback. Use get_state for media_player.salon_2. "
        "For requests to stop, pause, or turn off music in the salon/living room, call Home Assistant service domain script service turn_on with target.entity_id script.stop_music_in_salon. "
        "For requests like next song, skip, next track, or skip this song in the salon/living room, call Home Assistant service domain script service turn_on with target.entity_id script.next_song_in_salon. "
        "Use script.play_something_in_salon only for truly generic requests such as play something, play anything, or put on some music in the salon/living room when the user did not specify an artist, song, album, playlist, radio station, genre, or any other media choice. "
        "Generic music requests in the salon/living room should shuffle by default. "
        "For any salon/living room request that names an artist, song, album, playlist, radio station, genre, or other media choice, call Home Assistant service domain script service turn_on with target.entity_id script.play_music_in_salon and put the script inputs inside data.variables. "
        "For artist requests, set data.variables.media_type to artist, data.variables.media_id to the artist name, data.variables.artist to the artist name, data.variables.album to an empty string, data.variables.media_description to music by that artist, and data.variables.shuffle to false unless the user asked to shuffle. "
        "If the user asks to shuffle for a specific artist, song, album, or playlist request in the salon/living room, set data.variables.shuffle to true. "
        "Do not use script.play_something_in_salon for specific artist or playlist requests. Do not call the direct service script.play_music_in_salon. "
        "If the target device is ambiguous, ask a short follow-up only if needed. "
        "If the user indicates the conversation is over, says goodbye, says thanks and is done, or asks you to stop listening, call end_session. "
        "For state questions, use get_entities or get_state. "
        "For control requests, identify the entity and then call call_service. "
        "For current events, internet information, public facts beyond Home Assistant, or external services, use web_search. "
        "Keep spoken replies short and natural."
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

PRIVATE_SETTINGS = {"openai_api_key", "openai_admin_api_key"}

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
