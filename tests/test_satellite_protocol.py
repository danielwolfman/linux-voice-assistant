import json

import pytest

from linux_voice_assistant.audio.pcm import PcmFormat
from linux_voice_assistant.vape.protocol import (
    PROTOCOL_VERSION,
    ControlMessage,
    ProtocolError,
    build_control,
    negotiate_audio_format,
    parse_control,
)


def test_parse_control_requires_json_object_with_type():
    message = parse_control(json.dumps({"type": "wake_detected", "wake_word": "okay_nabu"}))

    assert message.type == "wake_detected"
    assert message.payload["wake_word"] == "okay_nabu"

    with pytest.raises(ProtocolError, match="JSON object"):
        parse_control("[]")

    with pytest.raises(ProtocolError, match="type"):
        parse_control("{}")


def test_build_control_adds_type_and_protocol_version():
    payload = build_control("hello_ack", selected_format={"codec": "pcm_s16le"})

    assert payload["type"] == "hello_ack"
    assert payload["protocol_version"] == PROTOCOL_VERSION
    assert payload["selected_format"] == {"codec": "pcm_s16le"}


def test_negotiate_audio_format_prefers_24k_pcm_then_48k():
    message = ControlMessage(
        type="hello",
        payload={
            "formats": [
                {"codec": "pcm_s16le", "sample_rate": 48000, "channels": 1},
                {"codec": "pcm_s16le", "sample_rate": 24000, "channels": 1},
            ]
        },
    )

    assert negotiate_audio_format(message) == PcmFormat(codec="pcm_s16le", sample_rate=24000, channels=1)


def test_negotiate_audio_format_rejects_unsupported_formats():
    message = ControlMessage(type="hello", payload={"formats": [{"codec": "opus", "sample_rate": 48000, "channels": 1}]})

    with pytest.raises(ProtocolError, match="No supported"):
        negotiate_audio_format(message)
