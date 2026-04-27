"""Control protocol for VAPE satellite WebSocket clients."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..audio.pcm import PcmFormat

PROTOCOL_VERSION = 1


class ProtocolError(ValueError):
    """Raised when a satellite protocol message is invalid."""


@dataclass(frozen=True)
class ControlMessage:
    type: str
    payload: dict[str, Any]


def parse_control(raw_message: str) -> ControlMessage:
    try:
        decoded = json.loads(raw_message)
    except json.JSONDecodeError as err:
        raise ProtocolError("Control message must be valid JSON") from err

    if not isinstance(decoded, dict):
        raise ProtocolError("Control message must be a JSON object")

    message_type = decoded.get("type")
    if not isinstance(message_type, str) or not message_type:
        raise ProtocolError("Control message requires a string type")

    return ControlMessage(type=message_type, payload=decoded)


def build_control(message_type: str, **payload: Any) -> dict[str, Any]:
    return {"type": message_type, "protocol_version": PROTOCOL_VERSION, **payload}


def negotiate_audio_format(message: ControlMessage) -> PcmFormat:
    formats = message.payload.get("formats")
    if not isinstance(formats, list):
        raise ProtocolError("hello message requires formats")

    candidates: list[PcmFormat] = []
    for item in formats:
        if not isinstance(item, dict):
            continue
        try:
            candidates.append(
                PcmFormat(
                    codec=str(item.get("codec", "")),
                    sample_rate=int(item.get("sample_rate", 0)),
                    channels=int(item.get("channels", 1)),
                )
            )
        except (TypeError, ValueError):
            continue

    for preferred_rate in (24000, 48000, 16000):
        for candidate in candidates:
            if candidate.sample_rate == preferred_rate:
                return candidate

    raise ProtocolError("No supported PCM audio format offered")
