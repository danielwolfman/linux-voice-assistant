import asyncio
import wave

from aiohttp.test_utils import TestClient, TestServer

from linux_voice_assistant.audio.pcm import PcmFormat
from linux_voice_assistant.tools.codex_agent import CodexJob
from linux_voice_assistant.tools.timer import TimerRecord
from linux_voice_assistant.vape.server import RemotePlaybackSink, SatelliteSessionHandler, VoiceSessionRegistry, create_app, create_session_factory, format_codex_completion_notification


class FakeController:
    def __init__(self):
        self.wake_words = []
        self.audio_chunks = []
        self.stopped = False

    def wakeup(self, wake_word):
        self.wake_words.append(wake_word)

    def handle_audio(self, audio_chunk: bytes) -> None:
        self.audio_chunks.append(audio_chunk)

    def stop(self) -> None:
        self.stopped = True


def test_satellite_server_handshake_and_audio_routes_to_controller():
    asyncio.run(_test_satellite_server_handshake_and_audio_routes_to_controller())


async def _test_satellite_server_handshake_and_audio_routes_to_controller():
    controller = FakeController()
    app = create_app(lambda _format, _send_json, _send_binary, _session_id: SatelliteSessionHandler(controller))
    client = TestClient(TestServer(app))
    await client.start_server()

    try:
        ws = await client.ws_connect("/vape")
        await ws.send_json(
            {
                "type": "hello",
                "device_id": "voice-pe-test",
                "formats": [{"codec": "pcm_s16le", "sample_rate": 24000, "channels": 1}],
            }
        )
        hello_ack = await ws.receive_json()

        await ws.send_json({"type": "wake_detected", "wake_word": "okay_nabu"})
        start_capture = await ws.receive_json()
        await ws.send_bytes(b"\x01\x00\x02\x00")
        await asyncio.sleep(0.01)

        assert hello_ack["type"] == "hello_ack"
        assert hello_ack["selected_format"]["sample_rate"] == 24000
        assert start_capture["type"] == "start_capture"
        assert controller.wake_words[0].wake_word == "okay_nabu"
        assert controller.audio_chunks == [b"\x01\x00\x02\x00"]

        await ws.close()
    finally:
        await client.close()


def test_remote_playback_sink_sends_start_audio_and_stop():
    asyncio.run(_test_remote_playback_sink_sends_start_audio_and_stop())


async def _test_remote_playback_sink_sends_start_audio_and_stop():
    sent_json = []
    sent_binary = []

    async def send_json(payload):
        sent_json.append(payload)

    async def send_binary(payload):
        sent_binary.append(payload)

    sink = RemotePlaybackSink(
        selected_input_format=PcmFormat(codec="pcm_s16le", sample_rate=24000, channels=1),
        output_sample_rate=48000,
        send_json=send_json,
        send_binary=send_binary,
    )

    sink.add_data(b"\x00\x00\x01\x00")
    await asyncio.sleep(0.01)
    sink.stop()
    await asyncio.sleep(0.01)

    assert sent_json[0]["type"] == "start_playback"
    assert sent_json[-1]["type"] == "stop_playback"
    assert len(sent_binary[0]) == 8
    assert sink.pending_samples == 0
    assert not sink.is_playing


def test_remote_playback_sink_streams_audio_file(tmp_path):
    asyncio.run(_test_remote_playback_sink_streams_audio_file(tmp_path))


def test_remote_playback_sink_serializes_audio_sends():
    asyncio.run(_test_remote_playback_sink_serializes_audio_sends())


async def _test_remote_playback_sink_serializes_audio_sends():
    sent_binary = []
    in_flight = 0
    max_in_flight = 0

    async def send_json(payload):
        del payload

    async def send_binary(payload):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.02)
        sent_binary.append(payload)
        in_flight -= 1

    sink = RemotePlaybackSink(
        selected_input_format=PcmFormat(codec="pcm_s16le", sample_rate=24000, channels=1),
        output_sample_rate=24000,
        send_json=send_json,
        send_binary=send_binary,
    )

    first = b"\x01\x00" * 120
    second = b"\x02\x00" * 120
    sink.add_data(first)
    sink.add_data(second)
    await asyncio.sleep(0.08)

    assert max_in_flight == 1
    assert sent_binary == [first, second]


async def _test_remote_playback_sink_streams_audio_file(tmp_path):
    sound_file = tmp_path / "cue.wav"
    with wave.open(str(sound_file), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(b"\x00\x00\x01\x00" * 120)

    sent_json = []
    sent_binary = []
    finished = asyncio.Event()

    async def send_json(payload):
        sent_json.append(payload)

    async def send_binary(payload):
        sent_binary.append(payload)

    sink = RemotePlaybackSink(
        selected_input_format=PcmFormat(codec="pcm_s16le", sample_rate=24000, channels=1),
        output_sample_rate=48000,
        send_json=send_json,
        send_binary=send_binary,
    )

    sink.play_file(str(sound_file), done_callback=finished.set)
    await asyncio.wait_for(finished.wait(), timeout=5)

    assert sent_json[0]["type"] == "start_playback"
    assert sent_binary
    assert sum(len(chunk) for chunk in sent_binary) > 0


def test_create_session_factory_builds_remote_playback_sink():
    created = {}

    def make_controller(audio_player, selected_format, session_id):
        created["audio_player"] = audio_player
        created["selected_format"] = selected_format
        created["session_id"] = session_id
        return FakeController()

    factory = create_session_factory(make_controller, output_sample_rate=24000)

    async def send_json(payload):
        created.setdefault("json", []).append(payload)

    async def send_binary(payload):
        created.setdefault("binary", []).append(payload)

    handler = factory(PcmFormat(codec="pcm_s16le", sample_rate=24000, channels=1), send_json, send_binary, "session-1")

    assert isinstance(handler, SatelliteSessionHandler)
    assert isinstance(created["audio_player"], RemotePlaybackSink)
    assert created["selected_format"].sample_rate == 24000
    assert created["session_id"] == "session-1"


def test_codex_completion_notification_is_short_and_status_aware(tmp_path):
    job = CodexJob(
        id="job-1",
        task="fix tests",
        workspace=tmp_path,
        execution_mode="docker",
        origin_session_id="session-1",
        status="succeeded",
        final_output="Changed files and tests pass.",
    )

    notification = format_codex_completion_notification(job)

    assert "Codex finished job job-1" in notification
    assert "Changed files and tests pass" in notification


def test_voice_session_registry_selects_origin_session(tmp_path):
    asyncio.run(_test_voice_session_registry_selects_origin_session(tmp_path))


async def _test_voice_session_registry_selects_origin_session(tmp_path):
    class IdleController:
        def __init__(self):
            self.notifications = []

        def can_accept_notification(self):
            return True

        async def speak_notification(self, notification, cue_sound=None):
            self.notifications.append((notification, cue_sound))
            return True

    registry = VoiceSessionRegistry()
    origin = IdleController()
    other = IdleController()
    registry.register("origin", origin)
    registry.register("other", other)

    job = CodexJob(
        id="job-2",
        task="fix tests",
        workspace=tmp_path,
        execution_mode="docker",
        origin_session_id="origin",
        status="succeeded",
        final_output="All done.",
    )

    await registry.notify_codex_job_finished(job)
    await asyncio.sleep(0.01)

    assert origin.notifications
    assert other.notifications == []


def test_voice_session_registry_routes_timer_finished_notification(tmp_path):
    asyncio.run(_test_voice_session_registry_routes_timer_finished_notification(tmp_path))


async def _test_voice_session_registry_routes_timer_finished_notification(tmp_path):
    del tmp_path

    class IdleController:
        def __init__(self):
            self.notifications = []

        def can_accept_notification(self):
            return True

        async def speak_notification(self, notification, cue_sound=None):
            self.notifications.append((notification, cue_sound))
            return True

    registry = VoiceSessionRegistry()
    origin = IdleController()
    other = IdleController()
    registry.register("origin", origin)
    registry.register("other", other)

    timer = TimerRecord(
        id="timer-1",
        duration_seconds=60,
        label="pasta",
        origin_session_id="origin",
        status="finished",
        finished_sound="/tmp/timer_finished.flac",
    )

    await registry.notify_timer_finished(timer)
    await asyncio.sleep(0.01)

    assert origin.notifications == [("The pasta timer is done.", "/tmp/timer_finished.flac")]
    assert other.notifications == []
