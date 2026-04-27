"""Frontend protocols for local and remote voice endpoints."""

from __future__ import annotations

from typing import Protocol


class AssistantPlaybackSink(Protocol):
    @property
    def is_playing(self) -> bool: ...

    @property
    def pending_samples(self) -> int: ...

    def set_volume(self, volume: float) -> None: ...

    def add_data(self, data: bytes) -> None: ...

    def stop(self) -> None: ...

    def close(self) -> None: ...
