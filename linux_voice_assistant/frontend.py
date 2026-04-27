"""Frontend protocols for local and remote voice endpoints."""

from __future__ import annotations

from typing import Protocol


class AssistantPlaybackSink(Protocol):
    @property
    def is_playing(self) -> bool:
        raise NotImplementedError

    @property
    def pending_samples(self) -> int:
        raise NotImplementedError

    def set_volume(self, volume: float) -> None:
        raise NotImplementedError

    def add_data(self, data: bytes) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError
