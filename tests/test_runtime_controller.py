import asyncio
import numpy as np
from types import SimpleNamespace

from linux_voice_assistant.audio.pcm import resample_pcm16_mono
from linux_voice_assistant.config import AppConfig
from linux_voice_assistant.frontend import AssistantPlaybackSink
from linux_voice_assistant.__main__ import _prepare_vape_server_config
from linux_voice_assistant.memory import InteractionMemoryStore
from linux_voice_assistant.realtime.client import _extract_assistant_transcript, classify_realtime_error
from linux_voice_assistant.runtime.controller import SessionController, SessionPhase, _detect_language, _estimate_realtime_cost_usd, _looks_like_question, pcm16_rms


def test_pcm16_rms_detects_signal_level():
    silent = np.zeros(320, dtype="<i2").tobytes()
    loud = (np.ones(320, dtype=np.float32) * 0.25 * 32767).astype("<i2").tobytes()

    assert pcm16_rms(silent) == 0.0
    assert pcm16_rms(loud) > 0.2


def test_estimate_realtime_cost_uses_model_alias():
    usage = {
        "input_text_tokens": 1000,
        "input_audio_tokens": 2000,
        "cached_input_tokens": 500,
        "output_text_tokens": 200,
        "output_audio_tokens": 3000,
    }

    cost = _estimate_realtime_cost_usd("gpt-realtime", usage)

    assert cost > 0


def test_resample_pcm16_mono_expands_to_target_rate():
    source = (np.arange(160, dtype=np.int16) - 80).astype("<i2").tobytes()

    resampled = resample_pcm16_mono(source, source_rate=16000, target_rate=24000)

    assert len(resampled) > len(source)
    assert len(resampled) % 2 == 0


def test_extract_assistant_transcript_prefers_audio_transcript_and_text():
    item = {
        "role": "assistant",
        "type": "message",
        "content": [
            {"type": "output_audio", "transcript": "hello there"},
            {"type": "output_text", "text": "general kenobi"},
        ],
    }

    assert _extract_assistant_transcript(item) == "hello there general kenobi"


def test_looks_like_question_detects_question_mark():
    assert _looks_like_question("Do you want me to turn it off?")
    assert _looks_like_question("האם לכבות את האור?")
    assert not _looks_like_question("I turned it off.")


def test_detect_language_identifies_hebrew_and_defaults_to_english():
    assert _detect_language("תבקש מקודקס לתקן את הטסטים") == "he"
    assert _detect_language("ask codex to fix the tests") == "en"


def test_classify_realtime_error_detects_quota_and_auth():
    assert classify_realtime_error("insufficient_quota: no credits left")[0] == "quota_billing"
    assert classify_realtime_error("invalid_api_key: unauthorized")[0] == "authentication"
    assert classify_realtime_error("service unavailable")[0] == "service_unavailable"


class FakePlaybackSink:
    def __init__(self):
        self.audio_chunks = []
        self.stopped = False
        self.closed = False
        self.volume = None
        self.is_playing = False
        self.pending_samples = 0

    def set_volume(self, volume: float) -> None:
        self.volume = volume

    def add_data(self, data: bytes) -> None:
        self.audio_chunks.append(data)
        self.is_playing = True
        self.pending_samples += len(data) // 2

    def stop(self) -> None:
        self.stopped = True
        self.is_playing = False
        self.pending_samples = 0

    def close(self) -> None:
        self.closed = True


def test_fake_playback_sink_satisfies_protocol():
    sink: AssistantPlaybackSink = FakePlaybackSink()

    sink.set_volume(0.5)
    sink.add_data(b"\x00\x00")
    sink.stop()
    sink.close()

    assert sink.volume == 0.5
    assert sink.audio_chunks == [b"\x00\x00"]
    assert sink.stopped
    assert sink.closed


def test_prepare_vape_server_config_keeps_backend_cues(tmp_path):
    config = AppConfig(
        name="test",
        config_path=None,
        frontend="vape-server",
        audio_input_device=None,
        audio_output_device=None,
        audio_input_block_size=1024,
        wakeup_sound="sounds/wake_word_triggered.flac",
        processing_sound="sounds/processing.wav",
        tool_call_sound="sounds/tool_call_processing.wav",
        session_end_sound="sounds/mute_switch_on.flac",
        timer_finished_sound="sounds/timer_finished.flac",
        wake_word_dirs=[],
        wake_model="hey_jarvis",
        stop_model="stop",
        download_dir=tmp_path,
        preferences_file=tmp_path / "preferences.json",
        refractory_seconds=2.0,
        openai_api_key="test",
        openai_model="gpt-realtime",
        openai_voice="coral",
        openai_api_base=None,
        openai_instructions="test",
        ha_url="http://127.0.0.1:8123",
        ha_token="test",
        ha_verify_ssl=False,
        session_timeout_seconds=20,
        vad_threshold=0.014,
        min_speech_seconds=0.2,
        end_silence_seconds=0.5,
        follow_up_after_tool_call=False,
        memory_interactions_count=6,
        enable_tool_get_entities=True,
        enable_tool_get_state=True,
        enable_tool_call_service=True,
        enable_tool_web_search=True,
        enable_tool_codex_agent=True,
        enable_tool_timer=True,
        enable_tool_discord=True,
        codex_jobs_dir=tmp_path / "codex_jobs",
        codex_workspace_dir=tmp_path,
        codex_docker_image="lva-codex-agent:latest",
        codex_host_codex_home=tmp_path / ".codex",
        codex_host_gh_config_dir=tmp_path / ".config" / "gh",
        codex_host_command="codex",
        codex_dispatch_mode="exec",
        codex_app_server_command="codex",
        discord_enabled=True,
        discord_bot_token="",
        discord_client_id="1504771552921518190",
        discord_allowed_user_ids="130283160301862913,468850569986179084",
        debug=False,
        vape_server_host="0.0.0.0",
        vape_server_port=8765,
        vape_server_path="/vape",
        vape_output_sample_rate=48000,
    )

    prepared = _prepare_vape_server_config(config)

    assert prepared.wakeup_sound is None
    assert prepared.session_end_sound is None
    assert prepared.processing_sound == "sounds/processing.wav"
    assert prepared.tool_call_sound == "sounds/tool_call_processing.wav"


class FakeRealtimeInput:
    def __init__(self):
        self.appended = []
        self.commits = 0

    async def append_input_audio(self, audio_chunk: bytes, source_rate: int) -> None:
        self.appended.append((audio_chunk, source_rate))

    async def commit_turn(self) -> None:
        self.commits += 1


def _controller_for_vad_test() -> SessionController:
    controller = object.__new__(SessionController)
    controller.state = SimpleNamespace(muted=False)
    controller.config = SimpleNamespace(
        frontend="local",
        vad_threshold=0.1,
        end_silence_seconds=99.0,
        min_speech_seconds=0.0,
        session_timeout_seconds=20.0,
        processing_sound=None,
    )
    controller.phase = SessionPhase.STREAMING_INPUT
    controller._input_sample_rate = 16000
    controller._session_deadline = None
    controller._mic_suppressed_until = 0.0
    controller._turn_open = False
    controller._speech_started_at = None
    controller._last_voice_at = None
    controller._last_audio_level_log_at = 0.0
    controller._wakeup_in_progress = False
    controller._last_remote_state = None
    controller._processing_sound_active = False
    controller._audio_player = SimpleNamespace(set_remote_state=lambda state: None)
    controller._realtime = FakeRealtimeInput()
    controller._schedule = lambda coroutine: __import__("asyncio").run(coroutine)
    controller._init_input_audio_buffers()
    return controller


def test_vad_flushes_preroll_audio_when_speech_starts():
    controller = _controller_for_vad_test()
    quiet = (np.ones(320, dtype=np.float32) * 0.03 * 32767).astype("<i2").tobytes()
    loud = (np.ones(320, dtype=np.float32) * 0.30 * 32767).astype("<i2").tobytes()

    controller.handle_audio(quiet)
    assert controller._realtime.appended == []

    controller.handle_audio(loud)

    assert controller._realtime.appended == [(quiet, 16000), (loud, 16000)]


def test_wakeup_startup_audio_is_buffered_until_streaming_begins():
    controller = _controller_for_vad_test()
    early = (np.ones(320, dtype=np.float32) * 0.25 * 32767).astype("<i2").tobytes()
    current = (np.ones(320, dtype=np.float32) * 0.30 * 32767).astype("<i2").tobytes()
    controller.phase = SessionPhase.IDLE
    controller._wakeup_in_progress = True

    controller.handle_audio(early)
    assert controller._realtime.appended == []

    controller.phase = SessionPhase.STREAMING_INPUT
    controller._wakeup_in_progress = False
    controller.handle_audio(current)

    assert controller._realtime.appended == [(early, 16000), (current, 16000)]


def test_vape_turn_end_uses_higher_threshold_than_speech_start():
    controller = _controller_for_vad_test()
    controller.config.frontend = "vape-server"
    controller.config.vad_threshold = 0.014
    controller.config.end_silence_seconds = 0.5
    speech = (np.ones(320, dtype=np.float32) * 0.08 * 32767).astype("<i2").tobytes()
    room_noise = (np.ones(320, dtype=np.float32) * 0.02 * 32767).astype("<i2").tobytes()

    controller.handle_audio(speech)
    assert controller._turn_open
    assert controller._last_voice_at is not None
    assert controller._speech_started_at is not None
    controller._last_voice_at -= 1.0
    controller._speech_started_at -= 1.0

    controller.handle_audio(room_noise)

    assert controller.phase == SessionPhase.SESSION_STARTING
    assert controller._realtime.commits == 1


def test_session_timeout_closes_quietly_without_end_sound():
    controller = object.__new__(SessionController)
    calls = []

    controller._set_phase = lambda phase: calls.append(("phase", phase))
    controller._reset_response_chain_state = lambda: calls.append(("reset", None))

    async def close_session_to_idle(*, play_end_sound: bool) -> None:
        calls.append(("close", play_end_sound))

    controller._close_session_to_idle = close_session_to_idle

    asyncio.run(controller._handle_timeout())

    assert ("phase", SessionPhase.SESSION_TIMEOUT) in calls
    assert ("reset", None) in calls
    assert ("close", False) in calls


def test_completed_response_persists_user_assistant_interaction(tmp_path):
    controller = object.__new__(SessionController)
    controller._notification_response_active = False
    controller._pending_user_transcript = "Turn on the kitchen lights"
    controller._pending_tool_memory = []
    controller._interaction_memory = InteractionMemoryStore(tmp_path / "interaction_memory.json")

    controller._remember_completed_interaction("Done.")

    recent = controller._interaction_memory.load_recent(1)
    assert len(recent) == 1
    assert recent[0].user == "Turn on the kitchen lights"
    assert recent[0].assistant == "Done."
    assert controller._pending_user_transcript is None


def test_completed_response_persists_tool_context_with_interaction(tmp_path):
    controller = object.__new__(SessionController)
    controller._notification_response_active = False
    controller._pending_user_transcript = "Send that link again"
    controller._pending_tool_memory = ["Requested Discord message to the configured Discord allowlist: https://example.com/show"]
    controller._interaction_memory = InteractionMemoryStore(tmp_path / "interaction_memory.json")

    controller._remember_completed_interaction("Sent.")

    recent = controller._interaction_memory.load_recent(1)
    assert len(recent) == 1
    assert "Sent." in recent[0].assistant
    assert "Action context from this turn:" in recent[0].assistant
    assert "https://example.com/show" in recent[0].assistant
    assert controller._pending_tool_memory == []


def test_refresh_realtime_memory_context_uses_configured_count(tmp_path):
    async def run():
        captured = []

        class FakeRealtimeMemory:
            async def update_memory_context(self, interactions):
                captured.extend(interactions)

        store = InteractionMemoryStore(tmp_path / "interaction_memory.json")
        for index in range(4):
            store.append(user=f"user {index}", assistant=f"assistant {index}")

        controller = object.__new__(SessionController)
        controller._interaction_memory = store
        controller.config = SimpleNamespace(memory_interactions_count=2)
        controller._realtime = FakeRealtimeMemory()

        await controller._refresh_realtime_memory_context()

        assert [interaction.user for interaction in captured] == ["user 2", "user 3"]

    asyncio.run(run())
