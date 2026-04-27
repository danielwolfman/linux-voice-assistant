import asyncio

from aiohttp.test_utils import TestClient, TestServer

from linux_voice_assistant.audio.pcm import PcmFormat
from linux_voice_assistant.vape.server import RemotePlaybackSink, SatelliteSessionHandler, create_app


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
    app = create_app(lambda _format, _send_json, _send_binary: SatelliteSessionHandler(controller))
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
