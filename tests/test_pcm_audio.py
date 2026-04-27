import numpy as np
import pytest

from linux_voice_assistant.audio.pcm import PcmFormat, pcm16_frame_bytes, resample_pcm16_mono


def test_pcm_format_accepts_supported_rates():
    audio_format = PcmFormat(codec="pcm_s16le", sample_rate=24000, channels=1)

    assert audio_format.bytes_per_second == 48000
    assert audio_format.frame_bytes(20) == 960


def test_pcm_format_rejects_unsupported_codec_and_channels():
    with pytest.raises(ValueError, match="codec"):
        PcmFormat(codec="opus", sample_rate=24000, channels=1)

    with pytest.raises(ValueError, match="mono"):
        PcmFormat(codec="pcm_s16le", sample_rate=24000, channels=2)


def test_pcm16_frame_bytes_rounds_to_whole_samples():
    assert pcm16_frame_bytes(sample_rate=24000, frame_ms=20) == 960
    assert pcm16_frame_bytes(sample_rate=48000, frame_ms=40) == 3840


def test_resample_pcm16_mono_supports_16k_24k_and_48k():
    source = (np.arange(160, dtype=np.int16) - 80).astype("<i2").tobytes()

    to_24k = resample_pcm16_mono(source, source_rate=16000, target_rate=24000)
    to_48k = resample_pcm16_mono(to_24k, source_rate=24000, target_rate=48000)
    back_to_24k = resample_pcm16_mono(to_48k, source_rate=48000, target_rate=24000)

    assert len(to_24k) == 480
    assert len(to_48k) == 960
    assert len(back_to_24k) == 480
