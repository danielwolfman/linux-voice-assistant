"""Small persistent interaction memory for Realtime wakeup context."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger(__name__)
_MAX_STORED_INTERACTIONS = 100


@dataclass(frozen=True)
class Interaction:
    user: str
    assistant: str
    timestamp: str


class InteractionMemoryStore:
    def __init__(self, path: Path, *, max_stored: int = _MAX_STORED_INTERACTIONS) -> None:
        self.path = path
        self.max_stored = max_stored

    def load_recent(self, count: int) -> list[Interaction]:
        if count <= 0:
            return []
        interactions = self._load_all()
        return interactions[-count:]

    def append(self, *, user: str, assistant: str) -> None:
        user = user.strip()
        assistant = assistant.strip()
        if not user or not assistant:
            return

        interactions = self._load_all()
        interactions.append(
            Interaction(
                user=user,
                assistant=assistant,
                timestamp=datetime.now(tz=UTC).isoformat(),
            )
        )
        interactions = interactions[-self.max_stored :]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"interactions": [asdict(interaction) for interaction in interactions]}
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _load_all(self) -> list[Interaction]:
        if not self.path.exists():
            return []
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            _LOGGER.exception("Failed to load interaction memory from %s", self.path)
            return []

        raw_interactions = loaded.get("interactions") if isinstance(loaded, dict) else None
        if not isinstance(raw_interactions, list):
            return []

        interactions: list[Interaction] = []
        for item in raw_interactions:
            parsed = _parse_interaction(item)
            if parsed is not None:
                interactions.append(parsed)
        return interactions[-self.max_stored :]


def _parse_interaction(value: Any) -> Interaction | None:
    if not isinstance(value, dict):
        return None
    user = str(value.get("user") or "").strip()
    assistant = str(value.get("assistant") or "").strip()
    timestamp = str(value.get("timestamp") or "").strip()
    if not user or not assistant:
        return None
    return Interaction(user=user, assistant=assistant, timestamp=timestamp)
