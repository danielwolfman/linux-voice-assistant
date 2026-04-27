"""PCM audio helpers used by local and remote voice frontends."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


SUPPORTED_PCM_SAMPLE_RATES = {16000, 24000, 48000}


@dataclass(frozen=True)
class PcmFormat:
    codec: str
    sample_rate: int
    channels: int = 1

    def __post_init__(self) -> None:
        if self.codec != "pcm_s16le":
            raise ValueError(f"Unsupported PCM codec: {self.codec}")
        if self.channels != 1:
            raise ValueError("Only mono PCM is supported")
        if self.sample_rate not in SUPPORTED_PCM_SAMPLE_RATES:
            raise ValueError(f"Unsupported PCM sample rate: {self.sample_rate}")

    @property
    def bytes_per_second(self) -> int:
        return self.sample_rate * self.channels * 2

    def frame_bytes(self, frame_ms: int) -> int:
        return pcm16_frame_bytes(sample_rate=self.sample_rate, frame_ms=frame_ms)


def pcm16_frame_bytes(*, sample_rate: int, frame_ms: int) -> int:
    samples = int(round(sample_rate * frame_ms / 1000))
    return samples * 2


def resample_pcm16_mono(audio_chunk: bytes, source_rate: int, target_rate: int) -> bytes:
    if source_rate == target_rate or not audio_chunk:
        return audio_chunk

    samples = np.frombuffer(audio_chunk, dtype="<i2")
    if samples.size == 0:
        return audio_chunk

    source_positions = np.arange(samples.size, dtype=np.float32)
    target_length = max(1, int(round(samples.size * target_rate / source_rate)))
    target_positions = np.linspace(0, samples.size - 1, num=target_length, dtype=np.float32)
    resampled = np.interp(target_positions, source_positions, samples.astype(np.float32))
    return np.clip(resampled, -32768, 32767).astype("<i2").tobytes()
