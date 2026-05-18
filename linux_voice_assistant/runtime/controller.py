"""Session controller for the Linux Realtime voice satellite."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from enum import Enum
from pathlib import Path
from typing import Optional, cast

import numpy as np

from ..config import AppConfig
from ..frontend import AssistantPlaybackSink
from ..ha_tools.activity_logger import HomeAssistantActivityLogger
from ..ha_tools.client import HomeAssistantToolBridge
from ..ha_tools.settings_listener import HomeAssistantSettingsListener
from ..memory import InteractionMemoryStore
from ..models import ServerState
from ..mpv_player import MpvMediaPlayer
from ..realtime.client import OpenAIRealtimeClient
from ..tools.codex_agent import CodexAgentTool, CodexJobManager
from ..tools.discord_bridge import DiscordBotService, DiscordTool
from ..tools.registry import ToolRegistry
from ..tools.timer import TimerManager, TimerTool
from ..tools.web_search import WebSearchTool

_LOGGER = logging.getLogger(__name__)
_INPUT_PREROLL_SECONDS = 1.2
_VAPE_END_VAD_THRESHOLD_FLOOR = 0.03


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
    def __init__(
        self,
        state: ServerState,
        config: AppConfig,
        loop: asyncio.AbstractEventLoop,
        audio_player: Optional[AssistantPlaybackSink] = None,
        input_sample_rate: int = 16000,
        codex_manager: Optional[CodexJobManager] = None,
        timer_manager: Optional[TimerManager] = None,
        discord_service: Optional[DiscordBotService] = None,
        session_id: Optional[str] = None,
    ) -> None:
        self.state = state
        self.config = config
        self.loop = loop
        self.session_id = session_id
        self._input_sample_rate = input_sample_rate
        self.phase = SessionPhase.IDLE
        self._session_deadline: Optional[float] = None
        self._mic_suppressed_until = 0.0
        self._follow_up_mic_holdoff_seconds = 2.5 if config.frontend == "vape-server" else 1.25
        self._assistant_audio_tail_seconds = 2.0 if config.frontend == "vape-server" else 0.75
        self._turn_open = False
        self._speech_started_at: Optional[float] = None
        self._last_voice_at: Optional[float] = None
        self._wakeup_in_progress = False
        self._init_input_audio_buffers()
        self._last_audio_level_log_at = 0.0
        self._processing_sound_active = False
        self._tool_sound_active = False
        self._error_sound_active = False
        self._logged_response_audio = False
        self._realtime_error_in_progress = False
        self._last_remote_state: Optional[str] = None
        self._tool_call_depth = 0
        self._tool_called_in_response_chain = False
        self._end_session_requested = False
        self._notification_response_active = False
        self._pending_user_transcript: Optional[str] = None
        self._pending_tool_memory: list[str] = []
        self._response_delay_task: Optional[asyncio.Task[None]] = None
        self._wakeup_sound_task: Optional[asyncio.Task[None]] = None
        self._interaction_memory = InteractionMemoryStore(config.download_dir / "interaction_memory.json")
        self._ha_tool_bridge = HomeAssistantToolBridge(config.ha_url, config.ha_token, verify_ssl=config.ha_verify_ssl)
        self._activity_logger = HomeAssistantActivityLogger(config.ha_url, config.ha_token, verify_ssl=config.ha_verify_ssl)
        codex_agent = CodexAgentTool(codex_manager, session_id, self._current_user_language) if codex_manager is not None else None
        timer_tool = TimerTool(timer_manager, session_id, self._ha_tool_bridge) if timer_manager is not None else None
        discord_tool = DiscordTool(discord_service) if discord_service is not None else None
        self._discord_service = discord_service
        self._tool_registry = ToolRegistry(self._ha_tool_bridge, WebSearchTool(), codex_agent=codex_agent, timer_tool=timer_tool, discord_tool=discord_tool)
        self._tool_registry.set_enabled_tools(_enabled_tools_from_config(config))
        from ..audio.realtime_player import RealtimeAudioPlayer

        self._audio_player = audio_player or RealtimeAudioPlayer(device=config.audio_output_device)
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

        if self.state.muted:
            return
        if self.phase != SessionPhase.STREAMING_INPUT:
            if self._wakeup_in_progress:
                self._remember_input_preroll(audio_chunk)
            return
        if now < self._mic_suppressed_until:
            return

        level = pcm16_rms(audio_chunk)
        end_threshold = _turn_end_threshold(self.config)
        if now - self._last_audio_level_log_at >= 1.0:
            self._last_audio_level_log_at = now
            _LOGGER.info(
                "Input audio level rms=%.4f start_threshold=%.4f end_threshold=%.4f",
                level,
                self.config.vad_threshold,
                end_threshold,
            )
        if not self._turn_open:
            if level < self.config.vad_threshold:
                self._remember_input_preroll(audio_chunk)
                return
            self._turn_open = True
            self._speech_started_at = now
            self._last_voice_at = now
            _LOGGER.info("Speech detected, opening turn (rms=%.4f threshold=%.4f)", level, self.config.vad_threshold)
            self._flush_input_preroll()
        else:
            if level >= end_threshold:
                self._last_voice_at = now

        self._session_deadline = now + self.config.session_timeout_seconds
        self._schedule(self._realtime.append_input_audio(audio_chunk, source_rate=self._input_sample_rate))

        if (
            self._turn_open
            and self._last_voice_at is not None
            and level < end_threshold
            and (now - self._last_voice_at) >= self.config.end_silence_seconds
            and self._speech_started_at is not None
            and (now - self._speech_started_at) >= self.config.min_speech_seconds
        ):
            self._turn_open = False
            self._speech_started_at = None
            self._last_voice_at = None
            self._set_phase(SessionPhase.SESSION_STARTING)
            _LOGGER.info("Committing turn after silence (rms=%.4f threshold=%.4f)", level, end_threshold)
            self._play_processing_sound()
            self._schedule(self._realtime.commit_turn())

    def wakeup(self, wake_word) -> None:
        wake_word_phrase = getattr(wake_word, "wake_word", getattr(wake_word, "id", "wake"))
        self._wakeup_in_progress = True
        self._clear_input_preroll()
        self._schedule(self._handle_wakeup(str(wake_word_phrase)))

    def stop(self) -> None:
        self._schedule(self._interrupt_and_listen())

    def is_microphone_blocked(self) -> bool:
        return (
            self.state.muted
            or self._error_sound_active
            or (time.monotonic() < self._mic_suppressed_until)
            or self.phase in {SessionPhase.PLAYING_OUTPUT, SessionPhase.TOOL_CALL}
            or self._audio_player.is_playing
        )

    def can_accept_notification(self) -> bool:
        return self.phase == SessionPhase.IDLE and not self._wakeup_in_progress and not self._audio_player.is_playing

    async def speak_notification(self, notification: str, cue_sound: Optional[str] = None) -> bool:
        if not self.can_accept_notification():
            return False
        self._notification_response_active = True
        await self._play_notification_cue(cue_sound)
        self._set_phase(SessionPhase.SESSION_STARTING)
        await self._realtime.create_text_response(
            "Speak this async notification to the user in one to three short sentences. "
            "If it asks for a target language, use that language. Then stop listening: "
            f"{notification}"
        )
        return True

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
            if self.config.frontend == "vape-server" and key in {
                "wakeup_sound",
                "processing_sound",
                "tool_call_sound",
                "session_end_sound",
                "vad_threshold",
                "end_silence_seconds",
            }:
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
            elif key == "discord_allowed_user_ids" and self._discord_service is not None:
                self._discord_service.set_allowed_user_ids(value)
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
            await self._refresh_realtime_memory_context()
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
        self._wakeup_in_progress = False
        self._session_deadline = time.monotonic() + self.config.session_timeout_seconds

    async def _interrupt_and_listen(self) -> None:
        self._wakeup_in_progress = False
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
        if not self._wakeup_in_progress:
            self._clear_input_preroll()
        if clear_remote_buffer:
            await self._realtime.clear_input_audio()

    def _init_input_audio_buffers(self) -> None:
        self._input_preroll: deque[bytes] = deque()
        self._input_preroll_bytes = 0
        self._input_preroll_limit_bytes = int(self._input_sample_rate * 2 * _INPUT_PREROLL_SECONDS)

    def _remember_input_preroll(self, audio_chunk: bytes) -> None:
        if not audio_chunk:
            return
        self._input_preroll.append(audio_chunk)
        self._input_preroll_bytes += len(audio_chunk)
        while self._input_preroll_bytes > self._input_preroll_limit_bytes and self._input_preroll:
            removed = self._input_preroll.popleft()
            self._input_preroll_bytes -= len(removed)

    def _flush_input_preroll(self) -> None:
        while self._input_preroll:
            audio_chunk = self._input_preroll.popleft()
            self._input_preroll_bytes -= len(audio_chunk)
            self._schedule(self._realtime.append_input_audio(audio_chunk, source_rate=self._input_sample_rate))
        self._input_preroll_bytes = 0

    def _clear_input_preroll(self) -> None:
        self._input_preroll.clear()
        self._input_preroll_bytes = 0

    async def _on_audio_delta(self, audio: bytes) -> None:
        if not self._logged_response_audio:
            _LOGGER.info("Realtime response audio started")
            self._logged_response_audio = True
        self._stop_processing_sound()
        self._stop_tool_sound()
        self._mic_suppressed_until = max(self._mic_suppressed_until, time.monotonic() + self._assistant_audio_tail_seconds)
        self._set_phase(SessionPhase.PLAYING_OUTPUT)
        self._session_deadline = time.monotonic() + self.config.session_timeout_seconds
        self._audio_player.add_data(audio)

    async def _on_response_created(self, response_id: str) -> None:
        self._logged_response_audio = False
        _LOGGER.info("Realtime response started: %s", response_id)

    async def _on_response_done(self, response_id: str, status: str, usage: dict[str, int], transcript: str, model: str) -> None:
        _LOGGER.info("Realtime response finished: %s (%s)", response_id, status)
        if transcript:
            _LOGGER.info("Realtime final assistant transcript: %s", transcript)
        _LOGGER.info("Realtime usage: %s", _format_usage_summary(model, usage))
        if self.phase == SessionPhase.TOOL_CALL:
            _LOGGER.debug("Ignoring intermediate response.done while awaiting additional tool or final answer")
            return

        should_end_session = self._should_end_session_after_response(transcript)
        self._remember_completed_interaction(transcript)

        if self._response_delay_task is not None:
            self._response_delay_task.cancel()

        if should_end_session:
            self._response_delay_task = asyncio.create_task(self._end_session_after_response())
        else:
            self._response_delay_task = asyncio.create_task(self._return_to_follow_up_listening())

    async def _on_tool_call_started(self, tool_name: str, arguments: dict[str, object]) -> None:
        _LOGGER.debug("Executing Home Assistant tool: %s", tool_name)
        await self._activity_logger.record_activity("tool_call", f"Started {tool_name} input={_compact_log_value(arguments, limit=1000)}")
        tool_memory = _format_tool_memory_start(tool_name, arguments)
        if tool_memory:
            self._pending_tool_memory.append(tool_memory)
        self._tool_call_depth += 1
        self._tool_called_in_response_chain = True
        self._start_tool_sound()
        self._set_phase(SessionPhase.TOOL_CALL)

    async def _on_tool_call_finished(self, tool_name: str, result: dict[str, object]) -> None:
        _LOGGER.debug("Finished Home Assistant tool: %s", tool_name)
        await self._activity_logger.record_activity("tool_call", f"Finished {tool_name} output={_compact_log_value(result, limit=1000)}")
        tool_memory = _format_tool_memory_result(tool_name, result)
        if tool_memory:
            self._pending_tool_memory.append(tool_memory)
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
            self._wakeup_in_progress = False
            await self._reset_turn(clear_remote_buffer=False)
            self.state.active_wake_words.discard(self.state.stop_word.id)
            self._session_deadline = None
            self._set_phase(SessionPhase.BACK_TO_IDLE)
            self._play_realtime_error_sound(reason)
            self._set_phase(SessionPhase.IDLE)
        finally:
            self._realtime_error_in_progress = False

    async def _on_user_transcript(self, transcript: str) -> None:
        self._pending_user_transcript = transcript
        self._pending_tool_memory = []
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
        _LOGGER.info("Session timed out after user silence; closing quietly")
        self._set_phase(SessionPhase.SESSION_TIMEOUT)
        self._reset_response_chain_state()
        await self._close_session_to_idle(play_end_sound=False)

    def _maybe_timeout(self, now: float) -> None:
        if self._session_deadline is None or self.phase not in {SessionPhase.STREAMING_INPUT, SessionPhase.IDLE}:
            return
        if now >= self._session_deadline:
            self._schedule(self._handle_timeout())

    def _set_phase(self, phase: SessionPhase) -> None:
        if phase != self.phase:
            _LOGGER.debug("Session phase: %s -> %s", self.phase.value, phase.value)
            self.phase = phase
            remote_state = {
                SessionPhase.IDLE: "idle",
                SessionPhase.WAKE_DETECTED: "listening",
                SessionPhase.STREAMING_INPUT: "listening",
                SessionPhase.INTERRUPTED: "listening",
                SessionPhase.SESSION_STARTING: "thinking",
                SessionPhase.TOOL_CALL: "thinking",
                SessionPhase.PLAYING_OUTPUT: "speaking",
                SessionPhase.SESSION_TIMEOUT: "idle",
                SessionPhase.BACK_TO_IDLE: "idle",
            }.get(phase)
            state_setter = getattr(self._audio_player, "set_remote_state", None)
            if remote_state and remote_state != self._last_remote_state and callable(state_setter):
                self._last_remote_state = remote_state
                state_setter(remote_state)

    def _schedule(self, coroutine) -> None:
        asyncio.run_coroutine_threadsafe(coroutine, self.loop)

    def _play_processing_sound(self) -> None:
        if not self.config.processing_sound:
            return
        if not Path(self.config.processing_sound).exists():
            return
        self._processing_sound_active = True
        remote_play_file = getattr(self._audio_player, "play_file", None)
        if callable(remote_play_file):
            remote_play_file(self.config.processing_sound, done_callback=self._on_processing_sound_finished)
        else:
            self.state.tts_player.play(self.config.processing_sound, done_callback=self._on_processing_sound_finished)

    def _stop_processing_sound(self) -> None:
        if not self._processing_sound_active:
            return
        self._processing_sound_active = False
        remote_stop_file = getattr(self._audio_player, "stop_file", None)
        if callable(remote_stop_file):
            remote_stop_file()
        else:
            self.state.tts_player.stop()

    def _on_processing_sound_finished(self) -> None:
        self._processing_sound_active = False

    def _should_end_session_after_response(self, transcript: str) -> bool:
        if self._notification_response_active:
            return True
        if not self._pending_user_transcript and not self._tool_called_in_response_chain:
            return True
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
        self._wakeup_in_progress = False
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
        remote_play_file = getattr(self._audio_player, "play_file", None)
        if callable(remote_play_file):
            remote_play_file(str(error_sound), done_callback=self._on_realtime_error_sound_finished)
        else:
            self.state.tts_player.play(str(error_sound), done_callback=self._on_realtime_error_sound_finished)

    def _on_realtime_error_sound_finished(self) -> None:
        self._error_sound_active = False

    async def _play_notification_cue(self, cue_sound: Optional[str]) -> None:
        if not cue_sound or not Path(cue_sound).exists():
            return
        finished = asyncio.Event()

        def _done() -> None:
            self.loop.call_soon_threadsafe(finished.set)

        self._mic_suppressed_until = max(self._mic_suppressed_until, time.monotonic() + 0.75)
        self._set_phase(SessionPhase.PLAYING_OUTPUT)
        remote_play_file = getattr(self._audio_player, "play_file", None)
        if callable(remote_play_file):
            remote_play_file(cue_sound, done_callback=_done)
        else:
            self.state.tts_player.play(cue_sound, done_callback=_done)
        try:
            await asyncio.wait_for(finished.wait(), timeout=8)
        except asyncio.TimeoutError:
            _LOGGER.warning("Timed out waiting for notification cue sound to finish: %s", cue_sound)

    def _reset_response_chain_state(self) -> None:
        self._tool_call_depth = 0
        self._tool_called_in_response_chain = False
        self._end_session_requested = False
        self._notification_response_active = False

    async def _refresh_realtime_memory_context(self) -> None:
        interactions = self._interaction_memory.load_recent(int(self.config.memory_interactions_count))
        _LOGGER.info("Loaded %s recent interaction(s) into wakeup memory context", len(interactions))
        await self._realtime.update_memory_context(interactions)

    def _remember_completed_interaction(self, assistant_transcript: str) -> None:
        if self._notification_response_active:
            return
        if not self._pending_user_transcript:
            return
        assistant_memory = _format_assistant_memory(assistant_transcript, self._pending_tool_memory)
        if not assistant_memory:
            return
        self._interaction_memory.append(user=self._pending_user_transcript, assistant=assistant_memory)
        self._pending_user_transcript = None
        self._pending_tool_memory = []

    def _current_user_language(self) -> str:
        return _detect_language(self._pending_user_transcript) if self._pending_user_transcript else ""

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
        remote_play_file = getattr(self._audio_player, "play_file", None)
        if callable(remote_play_file):
            remote_play_file(self.config.tool_call_sound, done_callback=self._on_tool_sound_finished)
        else:
            self._tool_sound_player.play(self.config.tool_call_sound, done_callback=self._on_tool_sound_finished)

    def _stop_tool_sound(self) -> None:
        if not self._tool_sound_active:
            return
        self._tool_sound_active = False
        remote_stop_file = getattr(self._audio_player, "stop_file", None)
        if callable(remote_stop_file):
            remote_stop_file()
        else:
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


def _turn_end_threshold(config: AppConfig) -> float:
    threshold = float(config.vad_threshold)
    if getattr(config, "frontend", None) == "vape-server":
        return max(threshold, _VAPE_END_VAD_THRESHOLD_FLOOR)
    return threshold


def _looks_like_question(transcript: str) -> bool:
    stripped = transcript.strip()
    return stripped.endswith("?") or stripped.endswith("؟")


def _detect_language(text: str) -> str:
    return "he" if any("\u0590" <= char <= "\u05ff" for char in text) else "en"


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
        "start_codex_task": config.enable_tool_codex_agent,
        "get_codex_status": config.enable_tool_codex_agent,
        "cancel_codex_task": config.enable_tool_codex_agent,
        "start_timer": config.enable_tool_timer,
        "get_timers": config.enable_tool_timer,
        "cancel_timer": config.enable_tool_timer,
        "send_discord_message": config.enable_tool_discord,
    }


def _compact_log_value(value: object, limit: int = 100) -> str:
    text = str(value).replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _format_assistant_memory(assistant_transcript: str, tool_memory: list[str]) -> str:
    parts = []
    transcript = assistant_transcript.strip()
    if transcript:
        parts.append(transcript)
    compact_tool_memory = [_compact_log_value(item, limit=1200) for item in tool_memory if item.strip()]
    if compact_tool_memory:
        parts.append("Action context from this turn:\n" + "\n".join(f"- {item}" for item in compact_tool_memory))
    return "\n\n".join(parts).strip()


def _format_tool_memory_start(tool_name: str, arguments: dict[str, object]) -> str:
    if tool_name == "send_discord_message":
        message = str(arguments.get("message") or "").strip()
        if not message:
            return ""
        recipients = arguments.get("user_ids")
        recipient_text = f" to Discord user ids {recipients}" if recipients else " to the configured Discord allowlist"
        return f"Requested Discord message{recipient_text}: {message}"
    if tool_name == "start_timer":
        return f"Started timer request: {_compact_log_value(arguments, limit=500)}"
    if tool_name == "start_codex_task":
        task = str(arguments.get("task") or "").strip()
        return f"Started Codex task: {task}" if task else ""
    return ""


def _format_tool_memory_result(tool_name: str, result: dict[str, object]) -> str:
    if tool_name == "send_discord_message":
        return f"Discord send result: {_compact_log_value(result, limit=500)}"
    if tool_name in {"web_search", "get_state", "call_service", "start_timer", "cancel_timer", "start_codex_task"}:
        return f"{tool_name} result: {_compact_log_value(result, limit=500)}"
    return ""


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
