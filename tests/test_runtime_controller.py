import numpy as np

from linux_voice_assistant.audio.pcm import resample_pcm16_mono
from linux_voice_assistant.frontend import AssistantPlaybackSink
from linux_voice_assistant.realtime.client import _extract_assistant_transcript, classify_realtime_error
from linux_voice_assistant.runtime.controller import _estimate_realtime_cost_usd, _looks_like_question, pcm16_rms


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
