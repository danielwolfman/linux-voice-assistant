"""Session controller for the Linux Realtime voice satellite."""

from __future__ import annotations

import asyncio
import logging
import time
from enum import Enum
from pathlib import Path
from typing import Optional, cast

import numpy as np

from ..config import AppConfig
from ..ha_tools.activity_logger import HomeAssistantActivityLogger
from ..ha_tools.client import HomeAssistantToolBridge
from ..ha_tools.settings_listener import HomeAssistantSettingsListener
from ..models import ServerState
from ..realtime.client import OpenAIRealtimeClient
from ..mpv_player import MpvMediaPlayer
from ..tools.registry import ToolRegistry
from ..tools.web_search import WebSearchTool

_LOGGER = logging.getLogger(__name__)


class SessionPhase(str, Enum):
    IDLE = "idle"
    WAKE_DETECTED = "wake_detected"
    SESSION_STARTING = "session_starting"
    STREAMING_INPUT = "streaming_input"
    PLAYING_OUTPUT = "playing_output"
    INTERRUPTED = "interrupted"
    TOOL_CALL = "tool_call"
    SESSION_TIMEOUT = "session_timeout"
    BACK_TO_IDLE = "back_to_idle"


class SessionController:
    def __init__(self, state: ServerState, config: AppConfig, loop: asyncio.AbstractEventLoop) -> None:
        self.state = state
        self.config = config
        self.loop = loop
        self.phase = SessionPhase.IDLE
        self._session_deadline: Optional[float] = None
        self._mic_suppressed_until = 0.0
        self._follow_up_mic_holdoff_seconds = 1.25
        self._assistant_audio_tail_seconds = 0.75
        self._turn_open = False
        self._speech_started_at: Optional[float] = None
        self._last_voice_at: Optional[float] = None
        self._processing_sound_active = False
        self._tool_sound_active = False
        self._error_sound_active = False
        self._realtime_error_in_progress = False
        self._tool_call_depth = 0
        self._tool_called_in_response_chain = False
        self._end_session_requested = False
        self._response_delay_task: Optional[asyncio.Task[None]] = None
        self._wakeup_sound_task: Optional[asyncio.Task[None]] = None
        self._ha_tool_bridge = HomeAssistantToolBridge(config.ha_url, config.ha_token, verify_ssl=config.ha_verify_ssl)
        self._activity_logger = HomeAssistantActivityLogger(config.ha_url, config.ha_token, verify_ssl=config.ha_verify_ssl)
        self._tool_registry = ToolRegistry(self._ha_tool_bridge, WebSearchTool())
        self._tool_registry.set_enabled_tools(_enabled_tools_from_config(config))
        from ..audio.realtime_player import RealtimeAudioPlayer

        self._audio_player = RealtimeAudioPlayer(device=config.audio_output_device)
        self._audio_player.set_volume(state.volume)
        self._tool_sound_player = MpvMediaPlayer()
        self._tool_sound_player.set_volume(int(round(state.volume * 100)))
        self._settings_listener = HomeAssistantSettingsListener(
            base_url=config.ha_url,
            token=config.ha_token,
            verify_ssl=config.ha_verify_ssl,
            on_update=self._apply_remote_settings,
        )
        self._realtime = OpenAIRealtimeClient(
            api_key=config.openai_api_key,
            model=config.openai_model,
            voice=config.openai_voice,
            instructions=config.openai_instructions,
            tools=self._tool_registry,
            api_base=config.openai_api_base,
            on_audio_delta=self._on_audio_delta,
            on_response_created=self._on_response_created,
            on_response_done=self._on_response_done,
            on_user_transcript=self._on_user_transcript,
            on_assistant_transcript=self._on_assistant_transcript,
            on_tool_call_started=self._on_tool_call_started,
            on_tool_call_finished=self._on_tool_call_finished,
            on_end_session_requested=self._on_end_session_requested,
            on_error=self._on_realtime_error,
        )

    async def start(self) -> None:
        await self._settings_listener.start()

    def handle_audio(self, audio_chunk: bytes) -> None:
        now = time.monotonic()
        self._maybe_timeout(now)

        if self.phase != SessionPhase.STREAMING_INPUT or self.state.muted:
            return

        level = pcm16_rms(audio_chunk)
        if not self._turn_open:
            if level < self.config.vad_threshold:
                return
            self._turn_open = True
            self._speech_started_at = now
            self._last_voice_at = now
            _LOGGER.debug("Speech detected, opening turn (rms=%.4f threshold=%.4f)", level, self.config.vad_threshold)
        else:
            if level >= self.config.vad_threshold:
                self._last_voice_at = now

        self._session_deadline = now + self.config.session_timeout_seconds
        self._schedule(self._realtime.append_input_audio(audio_chunk))

        if (
            self._turn_open
            and self._last_voice_at is not None
            and level < self.config.vad_threshold
            and (now - self._last_voice_at) >= self.config.end_silence_seconds
            and self._speech_started_at is not None
            and (now - self._speech_started_at) >= self.config.min_speech_seconds
        ):
            self._turn_open = False
            self._speech_started_at = None
            self._last_voice_at = None
            self._set_phase(SessionPhase.SESSION_STARTING)
            _LOGGER.debug("Committing turn after silence (rms=%.4f)", level)
            self._play_processing_sound()
            self._schedule(self._realtime.commit_turn())

    def wakeup(self, wake_word) -> None:
        wake_word_phrase = getattr(wake_word, "wake_word", getattr(wake_word, "id", "wake"))
        self._schedule(self._handle_wakeup(str(wake_word_phrase)))

    def stop(self) -> None:
        self._schedule(self._interrupt_and_listen())

    def is_microphone_blocked(self) -> bool:
        return self.state.muted or self._error_sound_active or (time.monotonic() < self._mic_suppressed_until) or self.phase in {SessionPhase.PLAYING_OUTPUT, SessionPhase.TOOL_CALL} or self._audio_player.is_playing

    async def shutdown(self) -> None:
        if self._response_delay_task is not None:
            self._response_delay_task.cancel()
        if self._wakeup_sound_task is not None:
            self._wakeup_sound_task.cancel()
        self._reset_response_chain_state()
        self._stop_tool_sound()
        self._audio_player.close()
        await self._settings_listener.close()
        await self._realtime.close()
        await self._activity_logger.close()
        await self._tool_registry.close()

    async def _apply_remote_settings(self, settings: dict[str, object]) -> None:
        changed_keys: list[str] = []
        new_model: Optional[str] = None
        new_voice: Optional[str] = None
        new_instructions: Optional[str] = None
        refresh_tools = False

        for key, value in settings.items():
            if not hasattr(self.config, key):
                continue
            current_value = getattr(self.config, key)
            if current_value == value:
                continue
            object.__setattr__(self.config, key, value)
            changed_keys.append(key)
            if key == "refractory_seconds":
                self.state.refractory_seconds = float(cast(float, value))
            elif key == "openai_model":
                new_model = str(value)
            elif key == "openai_voice":
                new_voice = str(value)
            elif key == "openai_instructions":
                new_instructions = str(value)
            elif key.startswith("enable_tool_"):
                refresh_tools = True

        if not changed_keys:
            return

        _LOGGER.info("Applied Home Assistant settings update: %s", ", ".join(changed_keys))
        if refresh_tools:
            self._tool_registry.set_enabled_tools(_enabled_tools_from_config(self.config))
        if new_model is not None or new_voice is not None or new_instructions is not None or refresh_tools:
            await self._realtime.update_session_settings(model=new_model, voice=new_voice, instructions=new_instructions)

    async def _handle_wakeup(self, wake_word_phrase: str) -> None:
        if self.state.muted:
            return

        if self.phase in {SessionPhase.PLAYING_OUTPUT, SessionPhase.SESSION_STARTING, SessionPhase.TOOL_CALL}:
            await self._interrupt_and_listen()
            return

        if self.phase == SessionPhase.STREAMING_INPUT:
            await self._reset_turn(clear_remote_buffer=True)
            return

        try:
            await self._realtime.connect()
        except Exception:
            return
        self._set_phase(SessionPhase.WAKE_DETECTED)
        self.state.active_wake_words.add(self.state.stop_word.id)
        if self.config.wakeup_sound and Path(self.config.wakeup_sound).exists():
            self._mic_suppressed_until = time.monotonic() + 0.35
            self._wakeup_sound_task = asyncio.create_task(self._play_wakeup_sound())
        await self._begin_listening()
        _LOGGER.info("Wake word detected: %s", wake_word_phrase)

    async def _play_wakeup_sound(self) -> None:
        finished = asyncio.Event()

        def _done() -> None:
            self.loop.call_soon_threadsafe(finished.set)

        self.state.tts_player.play(self.config.wakeup_sound or "", done_callback=_done)
        await finished.wait()

    async def _begin_listening(self) -> None:
        await self._reset_turn(clear_remote_buffer=True)
        self._reset_response_chain_state()
        self._set_phase(SessionPhase.STREAMING_INPUT)
        self._session_deadline = time.monotonic() + self.config.session_timeout_seconds

    async def _interrupt_and_listen(self) -> None:
        self._reset_response_chain_state()
        self._stop_processing_sound()
        self._stop_tool_sound()
        self.state.tts_player.stop()
        self._audio_player.stop()
        await self._realtime.cancel_response()
        await self._reset_turn(clear_remote_buffer=True)
        self._set_phase(SessionPhase.INTERRUPTED)
        self._set_phase(SessionPhase.STREAMING_INPUT)
        self._session_deadline = time.monotonic() + self.config.session_timeout_seconds

    async def _reset_turn(self, *, clear_remote_buffer: bool) -> None:
        self._turn_open = False
        self._speech_started_at = None
        self._last_voice_at = None
        if clear_remote_buffer:
            await self._realtime.clear_input_audio()

    async def _on_audio_delta(self, audio: bytes) -> None:
        self._stop_processing_sound()
        self._stop_tool_sound()
        self._mic_suppressed_until = max(self._mic_suppressed_until, time.monotonic() + self._assistant_audio_tail_seconds)
        self._set_phase(SessionPhase.PLAYING_OUTPUT)
        self._session_deadline = time.monotonic() + self.config.session_timeout_seconds
        self._audio_player.add_data(audio)

    async def _on_response_created(self, response_id: str) -> None:
        _LOGGER.debug("Realtime response started: %s", response_id)

    async def _on_response_done(self, response_id: str, status: str, usage: dict[str, int], transcript: str, model: str) -> None:
        _LOGGER.debug("Realtime response finished: %s (%s)", response_id, status)
        if transcript:
            _LOGGER.debug("Realtime final assistant transcript: %s", transcript)
        _LOGGER.debug("Realtime usage: %s", _format_usage_summary(model, usage))
        if self.phase == SessionPhase.TOOL_CALL:
            _LOGGER.debug("Ignoring intermediate response.done while awaiting additional tool or final answer")
            return

        if self._response_delay_task is not None:
            self._response_delay_task.cancel()

        if self._should_end_session_after_response(transcript):
            self._response_delay_task = asyncio.create_task(self._end_session_after_response())
        else:
            self._response_delay_task = asyncio.create_task(self._return_to_follow_up_listening())

    async def _on_tool_call_started(self, tool_name: str, arguments: dict[str, object]) -> None:
        _LOGGER.debug("Executing Home Assistant tool: %s", tool_name)
        await self._activity_logger.record_activity("tool_call", f"Started {tool_name} input={_compact_log_value(arguments)}")
        self._tool_call_depth += 1
        self._tool_called_in_response_chain = True
        self._start_tool_sound()
        self._set_phase(SessionPhase.TOOL_CALL)

    async def _on_tool_call_finished(self, tool_name: str, result: dict[str, object]) -> None:
        _LOGGER.debug("Finished Home Assistant tool: %s", tool_name)
        await self._activity_logger.record_activity("tool_call", f"Finished {tool_name} output={_compact_log_value(result)}")
        self._tool_call_depth = max(0, self._tool_call_depth - 1)

    async def _on_end_session_requested(self, reason: str) -> None:
        _LOGGER.debug("Session end requested by model: %s", reason)
        self._end_session_requested = True

    async def _on_realtime_error(self, reason: str, message: str) -> None:
        if self._realtime_error_in_progress:
            return

        self._realtime_error_in_progress = True
        try:
            _LOGGER.error("Realtime unavailable (%s): %s", reason, message)
            await self._activity_logger.record_activity("error", f"Realtime unavailable: {reason}", {"message": message})
            self._reset_response_chain_state()
            self._stop_processing_sound()
            self._stop_tool_sound()
            self._audio_player.stop()
            self.state.tts_player.stop()
            await self._realtime.close()
            await self._reset_turn(clear_remote_buffer=False)
            self.state.active_wake_words.discard(self.state.stop_word.id)
            self._session_deadline = None
            self._set_phase(SessionPhase.BACK_TO_IDLE)
            self._play_realtime_error_sound(reason)
            self._set_phase(SessionPhase.IDLE)
        finally:
            self._realtime_error_in_progress = False

    async def _on_user_transcript(self, transcript: str) -> None:
        await self._activity_logger.record_activity("user", transcript)

    async def _on_assistant_transcript(self, transcript: str) -> None:
        await self._activity_logger.record_activity("assistant", transcript)

    async def _return_to_follow_up_listening(self) -> None:
        await self._wait_for_output_drain()
        self._mic_suppressed_until = max(self._mic_suppressed_until, time.monotonic() + self._follow_up_mic_holdoff_seconds)
        self._reset_response_chain_state()
        if self.phase == SessionPhase.PLAYING_OUTPUT:
            self._set_phase(SessionPhase.STREAMING_INPUT)
        self._session_deadline = time.monotonic() + self.config.session_timeout_seconds

    async def _end_session_after_response(self) -> None:
        _LOGGER.debug("Ending session after response")
        await self._wait_for_output_drain()
        self._reset_response_chain_state()
        await self._close_session_to_idle(play_end_sound=True)

    async def _handle_timeout(self) -> None:
        self._set_phase(SessionPhase.SESSION_TIMEOUT)
        self._reset_response_chain_state()
        await self._close_session_to_idle(play_end_sound=True)

    def _maybe_timeout(self, now: float) -> None:
        if self._session_deadline is None or self.phase not in {SessionPhase.STREAMING_INPUT, SessionPhase.IDLE}:
            return
        if now >= self._session_deadline:
            self._schedule(self._handle_timeout())

    def _set_phase(self, phase: SessionPhase) -> None:
        if phase != self.phase:
            _LOGGER.debug("Session phase: %s -> %s", self.phase.value, phase.value)
            self.phase = phase

    def _schedule(self, coroutine) -> None:
        asyncio.run_coroutine_threadsafe(coroutine, self.loop)

    def _play_processing_sound(self) -> None:
        if not self.config.processing_sound:
            return
        if not Path(self.config.processing_sound).exists():
            return
        self._processing_sound_active = True
        self.state.tts_player.play(self.config.processing_sound, done_callback=self._on_processing_sound_finished)

    def _stop_processing_sound(self) -> None:
        if not self._processing_sound_active:
            return
        self._processing_sound_active = False
        self.state.tts_player.stop()

    def _on_processing_sound_finished(self) -> None:
        self._processing_sound_active = False

    def _should_end_session_after_response(self, transcript: str) -> bool:
        if _looks_like_question(transcript):
            return False
        if self._end_session_requested:
            return True
        if self._tool_called_in_response_chain and not self.config.follow_up_after_tool_call:
            return True
        return False

    async def _close_session_to_idle(self, *, play_end_sound: bool) -> None:
        _LOGGER.debug("Closing session to idle (play_end_sound=%s)", play_end_sound)
        self._stop_processing_sound()
        self._stop_tool_sound()
        self._audio_player.stop()
        self.state.tts_player.stop()
        await self._realtime.close()
        await self._reset_turn(clear_remote_buffer=False)
        self.state.active_wake_words.discard(self.state.stop_word.id)
        self._session_deadline = None
        self._set_phase(SessionPhase.BACK_TO_IDLE)
        if play_end_sound:
            self._play_session_end_sound()
        self._set_phase(SessionPhase.IDLE)

    def _play_session_end_sound(self) -> None:
        if not self.config.session_end_sound:
            return
        if not Path(self.config.session_end_sound).exists():
            return
        self._mic_suppressed_until = max(self._mic_suppressed_until, time.monotonic() + 0.4)
        self.state.tts_player.play(self.config.session_end_sound)

    def _play_realtime_error_sound(self, reason: str) -> None:
        error_sound = _realtime_error_sound_path(self.config.openai_voice, reason)
        if error_sound is None:
            _LOGGER.warning("No realtime error sound found for voice=%s reason=%s", self.config.openai_voice, reason)
            return
        self._error_sound_active = True
        self._mic_suppressed_until = max(self._mic_suppressed_until, time.monotonic() + 6.0)
        self.state.tts_player.play(str(error_sound), done_callback=self._on_realtime_error_sound_finished)

    def _on_realtime_error_sound_finished(self) -> None:
        self._error_sound_active = False

    def _reset_response_chain_state(self) -> None:
        self._tool_call_depth = 0
        self._tool_called_in_response_chain = False
        self._end_session_requested = False

    async def _wait_for_output_drain(self, stall_timeout_seconds: float = 8.0) -> None:
        last_pending_samples = self._audio_player.pending_samples
        deadline = time.monotonic() + stall_timeout_seconds
        while self._audio_player.is_playing:
            pending_samples = self._audio_player.pending_samples
            if pending_samples != last_pending_samples:
                last_pending_samples = pending_samples
                deadline = time.monotonic() + stall_timeout_seconds
            if time.monotonic() >= deadline:
                _LOGGER.warning(
                    "Playback drain stalled with pending_samples=%s; forcing stop",
                    pending_samples,
                )
                self._audio_player.stop()
                break
            await asyncio.sleep(0.05)

    def _start_tool_sound(self) -> None:
        if self._tool_sound_active:
            return
        if not self.config.tool_call_sound:
            return
        if not Path(self.config.tool_call_sound).exists():
            return
        self._tool_sound_active = True
        self._play_tool_sound_loop()

    def _play_tool_sound_loop(self) -> None:
        if not self._tool_sound_active or not self.config.tool_call_sound:
            return
        self._tool_sound_player.play(self.config.tool_call_sound, done_callback=self._on_tool_sound_finished)

    def _stop_tool_sound(self) -> None:
        if not self._tool_sound_active:
            return
        self._tool_sound_active = False
        self._tool_sound_player.stop()

    def _on_tool_sound_finished(self) -> None:
        if not self._tool_sound_active:
            return
        self._play_tool_sound_loop()


def pcm16_rms(audio_chunk: bytes) -> float:
    samples = np.frombuffer(audio_chunk, dtype="<i2")
    if samples.size == 0:
        return 0.0
    normalized = samples.astype(np.float32) / 32768.0
    return float(np.sqrt(np.mean(np.square(normalized))))


def _looks_like_question(transcript: str) -> bool:
    stripped = transcript.strip()
    return stripped.endswith("?") or stripped.endswith("؟")


def _realtime_error_sound_path(voice: str, reason: str) -> Optional[Path]:
    base_dir = Path(__file__).resolve().parents[2] / "sounds" / "openai_errors"
    selected_voice = voice if voice in _SUPPORTED_ERROR_VOICES else "marin"
    candidate = base_dir / selected_voice / f"{reason}.mp3"
    if candidate.exists():
        return candidate
    fallback = base_dir / "marin" / "generic.mp3"
    return fallback if fallback.exists() else None


def _format_usage_summary(model: str, usage: dict[str, int]) -> str:
    if not usage:
        return "usage unavailable"

    cost = _estimate_realtime_cost_usd(model, usage)
    return (
        f"input={usage.get('input_tokens', 0)} "
        f"(text={usage.get('input_text_tokens', 0)} audio={usage.get('input_audio_tokens', 0)} cached={usage.get('cached_input_tokens', 0)}), "
        f"output={usage.get('output_tokens', 0)} "
        f"(text={usage.get('output_text_tokens', 0)} audio={usage.get('output_audio_tokens', 0)}), "
        f"total={usage.get('total_tokens', 0)}, est_cost=${cost:.6f}"
    )


def _estimate_realtime_cost_usd(model: str, usage: dict[str, int]) -> float:
    pricing_key = _resolve_pricing_model(model)
    pricing = _REALTIME_PRICING.get(pricing_key)
    if pricing is None:
        return 0.0

    input_audio_tokens = usage.get("input_audio_tokens", 0)
    input_text_tokens = usage.get("input_text_tokens", 0)
    cached_input_tokens = min(usage.get("cached_input_tokens", 0), input_text_tokens)
    uncached_input_text_tokens = max(0, input_text_tokens - cached_input_tokens)
    output_audio_tokens = usage.get("output_audio_tokens", 0)
    output_text_tokens = usage.get("output_text_tokens", 0)

    return (
        (input_audio_tokens / 1_000_000) * pricing["audio_input"]
        + (cached_input_tokens / 1_000_000) * pricing["text_cached_input"]
        + (uncached_input_text_tokens / 1_000_000) * pricing["text_input"]
        + (output_audio_tokens / 1_000_000) * pricing["audio_output"]
        + (output_text_tokens / 1_000_000) * pricing["text_output"]
    )


_REALTIME_MODEL_ALIASES = {
    "gpt-realtime": "gpt-realtime-1.5",
    "gpt-4o-realtime-preview": "gpt-realtime-1.5",
}

_REALTIME_PRICING = {
    "gpt-realtime-1.5": {
        "audio_input": 32.00,
        "text_input": 4.00,
        "text_cached_input": 0.40,
        "audio_output": 64.00,
        "text_output": 16.00,
    },
    "gpt-realtime-mini": {
        "audio_input": 10.00,
        "text_input": 0.60,
        "text_cached_input": 0.06,
        "audio_output": 20.00,
        "text_output": 2.40,
    },
}

_SUPPORTED_ERROR_VOICES = {"alloy", "ash", "ballad", "cedar", "coral", "echo", "marin", "sage", "shimmer", "verse"}


def _enabled_tools_from_config(config: AppConfig) -> dict[str, bool]:
    return {
        "get_entities": config.enable_tool_get_entities,
        "get_state": config.enable_tool_get_state,
        "call_service": config.enable_tool_call_service,
        "web_search": config.enable_tool_web_search,
    }


def _compact_log_value(value: object, limit: int = 100) -> str:
    text = str(value).replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _resolve_pricing_model(model: str) -> str:
    if model in _REALTIME_PRICING:
        return model
    if model in _REALTIME_MODEL_ALIASES:
        return _REALTIME_MODEL_ALIASES[model]
    if model.startswith("gpt-realtime-mini"):
        return "gpt-realtime-mini"
    if model.startswith("gpt-realtime"):
        return "gpt-realtime-1.5"
    if model.startswith("gpt-4o-realtime-preview"):
        return "gpt-realtime-1.5"
    return model
