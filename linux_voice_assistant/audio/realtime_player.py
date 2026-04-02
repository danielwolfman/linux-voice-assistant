"""Low-latency PCM player for OpenAI Realtime audio."""

from __future__ import annotations

import logging
import threading
from typing import Optional

import numpy as np

_LOGGER = logging.getLogger(__name__)


class RealtimeAudioPlayer:
    def __init__(self, device: Optional[str] = None, sample_rate: int = 24000, channels: int = 1, blocksize: int = 1200) -> None:
        import sounddevice as sd  # type: ignore[import-untyped]

        _LOGGER.debug("Realtime audio output device: %s", device or "default")
        self._channels = channels
        self._lock = threading.Lock()
        self._playing = False
        self._stream_started = False
        self._queue: list[np.ndarray] = []
        self._queued_samples = 0
        self._volume = 1.0
        self._stream = sd.OutputStream(
            samplerate=sample_rate,
            channels=channels,
            dtype=np.int16,
            blocksize=blocksize,
            device=device,
            callback=self._callback,
        )

    @property
    def is_playing(self) -> bool:
        return self._playing

    @property
    def pending_samples(self) -> int:
        with self._lock:
            return self._queued_samples

    def set_volume(self, volume: float) -> None:
        with self._lock:
            self._volume = max(0.0, min(1.0, float(volume)))

    def add_data(self, data: bytes) -> None:
        if not data:
            return

        audio = np.frombuffer(data, dtype=np.int16)
        if audio.size == 0:
            return

        with self._lock:
            if self._volume != 1.0:
                scaled = np.clip(audio.astype(np.float32) * self._volume, -32768, 32767)
                audio = scaled.astype(np.int16)
            self._queue.append(audio)
            self._queued_samples += int(audio.size)
            self._playing = True
            if not self._stream_started:
                self._stream.start()
                self._stream_started = True

    def stop(self) -> None:
        should_stop_stream = False
        with self._lock:
            self._queue.clear()
            self._queued_samples = 0
            should_stop_stream = self._stream_started
            self._stream_started = False
            self._playing = False

        if should_stop_stream:
            self._stream.stop()

    def close(self) -> None:
        self.stop()
        self._stream.close()

    def _callback(self, outdata, frames, time_info, status) -> None:  # pragma: no cover - exercised indirectly on real devices
        del time_info, status
        with self._lock:
            data = np.empty(0, dtype=np.int16)
            consumed_samples = 0
            while len(data) < frames and self._queue:
                chunk = self._queue.pop(0)
                missing = frames - len(data)
                used = chunk[:missing]
                data = np.concatenate((data, used))
                consumed_samples += len(used)
                if len(chunk) > missing:
                    self._queue.insert(0, chunk[missing:])

            if len(data) < frames:
                data = np.concatenate((data, np.zeros(frames - len(data), dtype=np.int16)))

            self._queued_samples = max(0, self._queued_samples - consumed_samples)
            self._playing = bool(self._queue) or bool(np.any(data))

        outdata[:] = data.reshape(-1, self._channels)
