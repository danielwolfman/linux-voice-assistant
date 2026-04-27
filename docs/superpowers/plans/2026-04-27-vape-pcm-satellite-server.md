# VAPE PCM Satellite Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Linux-side VAPE satellite server that accepts wake/control events and lossless PCM microphone audio over WebSocket, forwards audio into the existing OpenAI Realtime session controller, and streams assistant PCM audio back to the satellite.

**Architecture:** Keep the existing local Linux microphone/speaker path as the default frontend. Add small audio/protocol boundaries so `SessionController` can route output to either the current local `RealtimeAudioPlayer` or a remote WebSocket playback sink. The first milestone implements and tests the Linux server plus a fake WebSocket client; VAPE firmware comes after this server is working.

**Tech Stack:** Python 3.11+, `aiohttp` WebSocket server, `numpy` PCM conversion, existing OpenAI Realtime client, existing Home Assistant tool bridge, `pytest`.

---

## File Structure

- Create `linux_voice_assistant/audio/pcm.py`
  - PCM format dataclass, sample-count helpers, and PCM16 mono resampling.
- Create `linux_voice_assistant/frontend.py`
  - Protocols for assistant playback sinks and frontend event notifications.
- Create `linux_voice_assistant/satellite/__init__.py`
  - Package marker for satellite server code.
- Create `linux_voice_assistant/satellite/protocol.py`
  - JSON control message parsing, validation, and audio negotiation.
- Create `linux_voice_assistant/satellite/server.py`
  - `aiohttp` WebSocket endpoint, one connected VAPE client per session for v1, remote playback sink.
- Create `tests/test_pcm_audio.py`
  - Unit tests for PCM format helpers and resampling.
- Create `tests/test_satellite_protocol.py`
  - Unit tests for protocol parsing and negotiation.
- Create `tests/test_satellite_server.py`
  - Async tests for WebSocket handshake, wake event, binary audio routing, and playback output.
- Modify `linux_voice_assistant/realtime/client.py`
  - Import resampling from `audio/pcm.py` and allow callers to provide the source input sample rate.
- Modify `linux_voice_assistant/runtime/controller.py`
  - Accept injectable playback sink and input sample rate; keep local player and 16 kHz local mic behavior as default.
- Modify `linux_voice_assistant/config.py`
  - Add VAPE server config and CLI flags.
- Modify `linux_voice_assistant/__main__.py`
  - Add `--frontend local|vape-server`; local remains default.
- Modify `examples/realtime-home-assistant.yaml`
  - Add commented VAPE server settings.
- Modify `README.md`
  - Document the Linux-side VAPE server milestone and fake-client test path.

---

### Task 1: PCM Audio Utilities

**Files:**
- Create: `linux_voice_assistant/audio/pcm.py`
- Modify: `linux_voice_assistant/realtime/client.py`
- Test: `tests/test_pcm_audio.py`
- Modify: `tests/test_runtime_controller.py`

- [ ] **Step 1: Write failing PCM utility tests**

Create `tests/test_pcm_audio.py`:

```python
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
```

Update `tests/test_runtime_controller.py` imports so `resample_pcm16_mono` comes from `linux_voice_assistant.audio.pcm`:

```python
from linux_voice_assistant.audio.pcm import resample_pcm16_mono
from linux_voice_assistant.realtime.client import _extract_assistant_transcript, classify_realtime_error
from linux_voice_assistant.runtime.controller import _estimate_realtime_cost_usd, _looks_like_question, pcm16_rms
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_pcm_audio.py tests/test_runtime_controller.py -q
```

Expected: FAIL because `linux_voice_assistant.audio.pcm` does not exist.

- [ ] **Step 3: Implement PCM utilities**

Create `linux_voice_assistant/audio/pcm.py`:

```python
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
```

Modify `linux_voice_assistant/realtime/client.py`:

```python
from ..audio.pcm import resample_pcm16_mono
```

Change `append_input_audio` so callers can preserve remote 24 kHz PCM instead of forcing the local Linux 16 kHz microphone assumption:

```python
async def append_input_audio(self, audio_chunk: bytes, *, source_rate: int = 16000) -> None:
    try:
        await self.connect()
        assert self._connection is not None
        realtime_chunk = resample_pcm16_mono(audio_chunk, source_rate=source_rate, target_rate=24000)
        await self._connection.send(
            {
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(realtime_chunk).decode("utf-8"),
            }
        )
    except asyncio.CancelledError:
        raise
    except Exception as err:
        await self._notify_error(err)
```

Remove the existing local `resample_pcm16_mono` function from the bottom of `realtime/client.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
pytest tests/test_pcm_audio.py tests/test_runtime_controller.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add linux_voice_assistant/audio/pcm.py linux_voice_assistant/realtime/client.py tests/test_pcm_audio.py tests/test_runtime_controller.py
git commit -m "feat: add shared PCM audio helpers"
```

---

### Task 2: Satellite Protocol Parser

**Files:**
- Create: `linux_voice_assistant/satellite/__init__.py`
- Create: `linux_voice_assistant/satellite/protocol.py`
- Test: `tests/test_satellite_protocol.py`

- [ ] **Step 1: Write failing protocol tests**

Create `tests/test_satellite_protocol.py`:

```python
import json

import pytest

from linux_voice_assistant.audio.pcm import PcmFormat
from linux_voice_assistant.satellite.protocol import (
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_satellite_protocol.py -q
```

Expected: FAIL because `linux_voice_assistant.satellite.protocol` does not exist.

- [ ] **Step 3: Implement protocol parser**

Create `linux_voice_assistant/satellite/__init__.py`:

```python
"""VAPE satellite server package."""
```

Create `linux_voice_assistant/satellite/protocol.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
pytest tests/test_satellite_protocol.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add linux_voice_assistant/satellite/__init__.py linux_voice_assistant/satellite/protocol.py tests/test_satellite_protocol.py
git commit -m "feat: add VAPE satellite protocol parser"
```

---

### Task 3: Injectable Playback Sink And Input Sample Rate

**Files:**
- Create: `linux_voice_assistant/frontend.py`
- Modify: `linux_voice_assistant/runtime/controller.py`
- Test: `tests/test_runtime_controller.py`

- [ ] **Step 1: Write failing playback sink test**

Append to `tests/test_runtime_controller.py`:

```python
from linux_voice_assistant.frontend import AssistantPlaybackSink


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
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_runtime_controller.py::test_fake_playback_sink_satisfies_protocol -q
```

Expected: FAIL because `linux_voice_assistant.frontend` does not exist.

- [ ] **Step 3: Add frontend protocols**

Create `linux_voice_assistant/frontend.py`:

```python
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
```

- [ ] **Step 4: Modify controller to accept injected sink**

Modify imports in `linux_voice_assistant/runtime/controller.py`:

```python
from ..frontend import AssistantPlaybackSink
```

Change `SessionController.__init__` signature:

```python
def __init__(
    self,
    state: ServerState,
    config: AppConfig,
    loop: asyncio.AbstractEventLoop,
    audio_player: Optional[AssistantPlaybackSink] = None,
    input_sample_rate: int = 16000,
) -> None:
```

Replace the direct player construction block:

```python
from ..audio.realtime_player import RealtimeAudioPlayer

self._audio_player = audio_player or RealtimeAudioPlayer(device=config.audio_output_device)
self._audio_player.set_volume(state.volume)
self._input_sample_rate = input_sample_rate
```

Keep all later uses of `self._audio_player` unchanged.

Change the audio append call in `handle_audio`:

```python
self._schedule(self._realtime.append_input_audio(audio_chunk, source_rate=self._input_sample_rate))
```

- [ ] **Step 5: Run targeted tests**

Run:

```bash
pytest tests/test_runtime_controller.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add linux_voice_assistant/frontend.py linux_voice_assistant/runtime/controller.py tests/test_runtime_controller.py
git commit -m "feat: allow remote assistant playback sinks"
```

---

### Task 4: VAPE Server Configuration

**Files:**
- Modify: `linux_voice_assistant/config.py`
- Modify: `examples/realtime-home-assistant.yaml`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write failing config test**

Append to `tests/test_config.py`:

```python
def test_load_config_reads_vape_server_options(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
home_assistant:
  url: http://yaml.local:8123
  token: yaml-token
openai:
  api_key: yaml-openai
vape_server:
  host: 0.0.0.0
  port: 8765
  path: /vape
  output_sample_rate: 48000
""",
        encoding="utf-8",
    )

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("HOME_ASSISTANT_URL", raising=False)
    monkeypatch.delenv("HOME_ASSISTANT_TOKEN", raising=False)

    config, _ = load_config(["--config", os.fspath(config_path), "--frontend", "vape-server"])

    assert config.frontend == "vape-server"
    assert config.vape_server_host == "0.0.0.0"
    assert config.vape_server_port == 8765
    assert config.vape_server_path == "/vape"
    assert config.vape_output_sample_rate == 48000
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_config.py::test_load_config_reads_vape_server_options -q
```

Expected: FAIL because config fields and `--frontend` do not exist.

- [ ] **Step 3: Add config fields and CLI flags**

Modify `AppConfig` in `linux_voice_assistant/config.py` with:

```python
frontend: str
vape_server_host: str
vape_server_port: int
vape_server_path: str
vape_output_sample_rate: int
```

Add parser arguments:

```python
parser.add_argument("--frontend", choices=["local", "vape-server"], help="Audio frontend to run")
parser.add_argument("--vape-server-host", help="Host/IP for the VAPE satellite WebSocket server")
parser.add_argument("--vape-server-port", type=int, help="Port for the VAPE satellite WebSocket server")
parser.add_argument("--vape-server-path", help="WebSocket path for VAPE satellite clients")
parser.add_argument("--vape-output-sample-rate", type=int, choices=[24000, 48000], help="PCM sample rate sent back to VAPE")
```

Set config values in `AppConfig(...)`:

```python
frontend=str(_pick(args.frontend, os.getenv("LVA_FRONTEND"), _get_str(yaml_config, "frontend"), "local")),
vape_server_host=str(_pick(args.vape_server_host, os.getenv("LVA_VAPE_SERVER_HOST"), _get_str(yaml_config, "vape_server.host"), "0.0.0.0")),
vape_server_port=int(_pick(args.vape_server_port, _env_int("LVA_VAPE_SERVER_PORT"), _get_int(yaml_config, "vape_server.port"), 8765)),
vape_server_path=str(_pick(args.vape_server_path, os.getenv("LVA_VAPE_SERVER_PATH"), _get_str(yaml_config, "vape_server.path"), "/vape")),
vape_output_sample_rate=int(_pick(args.vape_output_sample_rate, _env_int("LVA_VAPE_OUTPUT_SAMPLE_RATE"), _get_int(yaml_config, "vape_server.output_sample_rate"), 24000)),
```

- [ ] **Step 4: Document example YAML**

Append to `examples/realtime-home-assistant.yaml`:

```yaml
# frontend: local

# vape_server:
#   host: 0.0.0.0
#   port: 8765
#   path: /vape
#   output_sample_rate: 24000
```

- [ ] **Step 5: Run config tests**

Run:

```bash
pytest tests/test_config.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add linux_voice_assistant/config.py examples/realtime-home-assistant.yaml tests/test_config.py
git commit -m "feat: add VAPE server configuration"
```

---

### Task 5: WebSocket Satellite Server Core

**Files:**
- Create: `linux_voice_assistant/satellite/server.py`
- Test: `tests/test_satellite_server.py`

- [ ] **Step 1: Write failing server tests**

Create `tests/test_satellite_server.py`:

```python
import asyncio

import pytest
from aiohttp.test_utils import TestClient, TestServer

from linux_voice_assistant.audio.pcm import PcmFormat
from linux_voice_assistant.satellite.server import RemotePlaybackSink, SatelliteSessionHandler, create_app


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


@pytest.mark.asyncio
async def test_satellite_server_handshake_and_audio_routes_to_controller():
    controller = FakeController()
    app = create_app(lambda _format, _send_json, _send_binary: SatelliteSessionHandler(controller))
    client = TestClient(TestServer(app))
    await client.start_server()

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

    assert hello_ack["type"] == "hello_ack"
    assert hello_ack["selected_format"]["sample_rate"] == 24000
    assert start_capture["type"] == "start_capture"
    assert controller.wake_words[0].wake_word == "okay_nabu"
    assert controller.audio_chunks == [b"\x01\x00\x02\x00"]

    await ws.close()
    await client.close()


@pytest.mark.asyncio
async def test_remote_playback_sink_sends_start_audio_and_stop():
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_satellite_server.py -q
```

Expected: FAIL because `satellite/server.py` does not exist.

- [ ] **Step 3: Implement satellite server**

Create `linux_voice_assistant/satellite/server.py`:

```python
"""WebSocket server for VAPE PCM satellite clients."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from aiohttp import WSMsgType, web

from ..audio.pcm import PcmFormat, resample_pcm16_mono
from .protocol import ProtocolError, build_control, negotiate_audio_format, parse_control

_LOGGER = logging.getLogger(__name__)

SendJson = Callable[[dict], Awaitable[None]]
SendBinary = Callable[[bytes], Awaitable[None]]


@dataclass(frozen=True)
class RemoteWakeWord:
    id: str
    wake_word: str


class RemotePlaybackSink:
    def __init__(
        self,
        *,
        selected_input_format: PcmFormat,
        output_sample_rate: int,
        send_json: SendJson,
        send_binary: SendBinary,
    ) -> None:
        self._selected_input_format = selected_input_format
        self._output_sample_rate = output_sample_rate
        self._send_json = send_json
        self._send_binary = send_binary
        self._pending_samples = 0
        self._playing = False
        self._started = False
        self._closed = False

    @property
    def is_playing(self) -> bool:
        return self._playing

    @property
    def pending_samples(self) -> int:
        return self._pending_samples

    def set_volume(self, volume: float) -> None:
        del volume

    def add_data(self, data: bytes) -> None:
        if self._closed or not data:
            return
        asyncio.create_task(self._send_audio(data))

    def stop(self) -> None:
        self._pending_samples = 0
        self._playing = False
        self._started = False
        if not self._closed:
            asyncio.create_task(self._send_json(build_control("stop_playback")))

    def close(self) -> None:
        self._closed = True
        self.stop()

    async def _send_audio(self, data: bytes) -> None:
        if not self._started:
            await self._send_json(
                build_control(
                    "start_playback",
                    format={"codec": "pcm_s16le", "sample_rate": self._output_sample_rate, "channels": 1},
                )
            )
            self._started = True

        output = resample_pcm16_mono(data, source_rate=24000, target_rate=self._output_sample_rate)
        self._pending_samples += len(output) // 2
        self._playing = True
        await self._send_binary(output)
        self._pending_samples = max(0, self._pending_samples - (len(output) // 2))
        self._playing = self._pending_samples > 0


class SatelliteSessionHandler:
    def __init__(self, controller) -> None:
        self._controller = controller

    async def handle_control(self, raw_message: str, send_json: SendJson) -> None:
        message = parse_control(raw_message)
        if message.type == "wake_detected":
            wake_word = str(message.payload.get("wake_word") or "wake")
            self._controller.wakeup(RemoteWakeWord(id=wake_word, wake_word=wake_word))
            await send_json(build_control("start_capture"))
            return
        if message.type == "audio_stop":
            self._controller.stop()
            return
        if message.type in {"ping", "playback_done", "mute_changed", "button"}:
            if message.type == "ping":
                await send_json(build_control("pong"))
            return
        raise ProtocolError(f"Unsupported control message: {message.type}")

    def handle_audio(self, audio_chunk: bytes) -> None:
        self._controller.handle_audio(audio_chunk)


SessionFactory = Callable[[PcmFormat, SendJson, SendBinary], SatelliteSessionHandler]


def create_app(session_factory: SessionFactory, *, path: str = "/vape") -> web.Application:
    app = web.Application()

    async def websocket_handler(request: web.Request) -> web.WebSocketResponse:
        websocket = web.WebSocketResponse()
        await websocket.prepare(request)

        async def send_json(payload: dict) -> None:
            await websocket.send_json(payload)

        async def send_binary(payload: bytes) -> None:
            await websocket.send_bytes(payload)

        selected_format: Optional[PcmFormat] = None
        handler: Optional[SatelliteSessionHandler] = None

        try:
            async for ws_message in websocket:
                if ws_message.type == WSMsgType.TEXT:
                    control = parse_control(ws_message.data)
                    if control.type == "hello":
                        selected_format = negotiate_audio_format(control)
                        handler = session_factory(selected_format, send_json, send_binary)
                        await send_json(
                            build_control(
                                "hello_ack",
                                selected_format={"codec": selected_format.codec, "sample_rate": selected_format.sample_rate, "channels": selected_format.channels},
                            )
                        )
                        continue
                    if handler is None:
                        raise ProtocolError("hello must be sent before other messages")
                    await handler.handle_control(ws_message.data, send_json)
                elif ws_message.type == WSMsgType.BINARY:
                    if handler is None or selected_format is None:
                        raise ProtocolError("hello must be sent before audio")
                    handler.handle_audio(ws_message.data)
                elif ws_message.type == WSMsgType.ERROR:
                    _LOGGER.warning("VAPE WebSocket error: %s", websocket.exception())
        except ProtocolError as err:
            await websocket.send_json(build_control("error", code="protocol_error", message=str(err)))
            await websocket.close()

        return websocket

    app.router.add_get(path, websocket_handler)
    return app
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
pytest tests/test_satellite_server.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add linux_voice_assistant/satellite/server.py tests/test_satellite_server.py
git commit -m "feat: add VAPE WebSocket satellite server"
```

---

### Task 6: Wire VAPE Server Into CLI Runtime

**Files:**
- Modify: `linux_voice_assistant/__main__.py`
- Modify: `linux_voice_assistant/satellite/server.py`
- Test: `tests/test_satellite_server.py`

- [ ] **Step 1: Add runtime server helper test**

Append to `tests/test_satellite_server.py`:

```python
def test_create_session_factory_builds_remote_playback_sink():
    created = {}

    def make_controller(audio_player, selected_format):
        created["audio_player"] = audio_player
        created["selected_format"] = selected_format
        return FakeController()

    from linux_voice_assistant.satellite.server import create_session_factory

    factory = create_session_factory(make_controller, output_sample_rate=24000)

    async def send_json(payload):
        created.setdefault("json", []).append(payload)

    async def send_binary(payload):
        created.setdefault("binary", []).append(payload)

    handler = factory(PcmFormat(codec="pcm_s16le", sample_rate=24000, channels=1), send_json, send_binary)

    assert isinstance(handler, SatelliteSessionHandler)
    assert isinstance(created["audio_player"], RemotePlaybackSink)
    assert created["selected_format"].sample_rate == 24000
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_satellite_server.py::test_create_session_factory_builds_remote_playback_sink -q
```

Expected: FAIL because `create_session_factory` does not exist.

- [ ] **Step 3: Add session factory helper**

Add to `linux_voice_assistant/satellite/server.py`:

```python
def create_session_factory(make_controller: Callable[[RemotePlaybackSink, PcmFormat], object], *, output_sample_rate: int) -> SessionFactory:
    def factory(selected_format: PcmFormat, send_json: SendJson, send_binary: SendBinary) -> SatelliteSessionHandler:
        sink = RemotePlaybackSink(
            selected_input_format=selected_format,
            output_sample_rate=output_sample_rate,
            send_json=send_json,
            send_binary=send_binary,
        )
        return SatelliteSessionHandler(make_controller(sink, selected_format))

    return factory
```

- [ ] **Step 4: Add VAPE server branch in `__main__.py`**

Refactor `main()` in `linux_voice_assistant/__main__.py` just enough to support two runtime paths.

Keep the existing local path in a helper:

```python
async def run_local_frontend(config: AppConfig, args) -> None:
    ...
```

Move the current post-device-listing runtime body into that helper.

Add a VAPE server helper:

```python
async def run_vape_server_frontend(config: AppConfig) -> None:
    from aiohttp import web

    from .satellite.server import create_app, create_session_factory

    logging.basicConfig(level=logging.DEBUG if config.debug else logging.INFO)
    preferences_path = config.preferences_file
    preferences = _load_preferences(preferences_path)
    initial_volume = preferences.volume if preferences.volume is not None else 1.0
    preferences.volume = max(0.0, min(1.0, float(initial_volume)))

    loop = asyncio.get_running_loop()

    def make_controller(audio_player, selected_format):
        state = build_server_state_for_vape(config, preferences)
        controller = SessionController(
            state=state,
            config=config,
            loop=loop,
            audio_player=audio_player,
            input_sample_rate=selected_format.sample_rate,
        )
        state.satellite = controller
        loop.create_task(controller.start())
        return controller

    app = create_app(
        create_session_factory(make_controller, output_sample_rate=config.vape_output_sample_rate),
        path=config.vape_server_path,
    )
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, config.vape_server_host, config.vape_server_port)
    await site.start()
    _LOGGER.info("VAPE satellite server listening on ws://%s:%s%s", config.vape_server_host, config.vape_server_port, config.vape_server_path)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
```

Add a focused helper that creates `ServerState` without local mic wake models:

```python
def build_server_state_for_vape(config: AppConfig, preferences: Preferences) -> ServerState:
    network_interface = get_default_interface() or "unknown"
    mac_address = get_mac_address(interface=network_interface) or get_mac_address() or "00:00:00:00:00:00"
    mac_address_clean = mac_address.replace(":", "").lower()
    friendly_name = config.name or f"LVA VAPE Server - {mac_address_clean}"
    device_name = f"lva-vape-server-{mac_address_clean}"
    initial_volume = preferences.volume if preferences.volume is not None else 1.0

    return ServerState(
        name=device_name,
        friendly_name=friendly_name,
        network_interface=network_interface,
        mac_address=mac_address,
        ip_address="127.0.0.1",
        version=get_version(),
        esphome_version="vape-server",
        audio_queue=Queue(),
        entities=[],
        available_wake_words={},
        wake_words={},
        active_wake_words=set(),
        stop_word=NullStopWord(),
        music_player=MpvMediaPlayer(),
        tts_player=MpvMediaPlayer(),
        wakeup_sound=config.wakeup_sound or "",
        timer_finished_sound="",
        processing_sound=config.processing_sound or "",
        mute_sound="",
        unmute_sound="",
        preferences=preferences,
        preferences_path=config.preferences_file,
        download_dir=config.download_dir,
        refractory_seconds=config.refractory_seconds,
        output_only=False,
        volume=max(0.0, min(1.0, float(initial_volume))),
        timer_max_ring_seconds=0.0,
    )
```

Add:

```python
class NullStopWord:
    id = "stop"
```

Change `main()` after list-device handling:

```python
if config.frontend == "vape-server":
    await run_vape_server_frontend(config)
else:
    await run_local_frontend(config, args)
```

- [ ] **Step 5: Run targeted tests**

Run:

```bash
pytest tests/test_satellite_server.py tests/test_config.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add linux_voice_assistant/__main__.py linux_voice_assistant/satellite/server.py tests/test_satellite_server.py
git commit -m "feat: wire VAPE satellite server into runtime"
```

---

### Task 7: Fake WebSocket Client Smoke Script

**Files:**
- Create: `examples/vape_fake_client.py`
- Modify: `README.md`

- [ ] **Step 1: Add fake client script**

Create `examples/vape_fake_client.py`:

```python
#!/usr/bin/env python3
"""Small VAPE protocol smoke client for Linux-side testing."""

from __future__ import annotations

import argparse
import asyncio
import wave

import aiohttp


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="ws://127.0.0.1:8765/vape")
    parser.add_argument("--wav", required=True, help="16-bit mono WAV file to stream")
    parser.add_argument("--frame-ms", type=int, default=20)
    args = parser.parse_args()

    with wave.open(args.wav, "rb") as wav_file:
        if wav_file.getsampwidth() != 2 or wav_file.getnchannels() != 1:
            raise SystemExit("WAV must be 16-bit mono PCM")
        sample_rate = wav_file.getframerate()
        frame_bytes = int(sample_rate * args.frame_ms / 1000) * 2

        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(args.url) as websocket:
                await websocket.send_json(
                    {
                        "type": "hello",
                        "device_id": "fake-vape-client",
                        "formats": [{"codec": "pcm_s16le", "sample_rate": sample_rate, "channels": 1}],
                    }
                )
                print(await websocket.receive_json())

                await websocket.send_json({"type": "wake_detected", "wake_word": "fake_wake"})
                print(await websocket.receive_json())

                while True:
                    chunk = wav_file.readframes(frame_bytes // 2)
                    if not chunk:
                        break
                    await websocket.send_bytes(chunk)
                    await asyncio.sleep(args.frame_ms / 1000)

                await websocket.send_json({"type": "audio_stop"})
                async for message in websocket:
                    if message.type == aiohttp.WSMsgType.TEXT:
                        print(message.data)
                    elif message.type == aiohttp.WSMsgType.BINARY:
                        print(f"audio bytes: {len(message.data)}")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Document smoke test**

Add to `README.md` under the Realtime/Docker run area:

````markdown
## VAPE Satellite Server Preview

The Linux-side VAPE server is started with:

```sh
linux-voice-assistant --config realtime-home-assistant.yaml --frontend vape-server --debug
```

It listens on `ws://0.0.0.0:8765/vape` by default and expects a VAPE-compatible client to send JSON control frames and binary PCM16 mono audio frames. Before custom VAPE firmware is flashed, use the fake client with a 16-bit mono WAV file:

```sh
python examples/vape_fake_client.py --url ws://127.0.0.1:8765/vape --wav sample-command.wav
```
````

- [ ] **Step 3: Run syntax check**

Run:

```bash
python -m py_compile examples/vape_fake_client.py
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add examples/vape_fake_client.py README.md
git commit -m "docs: add VAPE fake client smoke test"
```

---

### Task 8: Full Verification

**Files:**
- No new files unless fixes are needed.

- [ ] **Step 1: Run unit tests**

Run:

```bash
pytest tests/test_pcm_audio.py tests/test_satellite_protocol.py tests/test_satellite_server.py tests/test_config.py tests/test_runtime_controller.py tests/test_ha_tools.py -q
```

Expected: PASS.

- [ ] **Step 2: Run lint if local environment supports it**

Run:

```bash
./script/lint
```

Expected: PASS. If the environment lacks system audio dependencies, record the exact failure and run the narrower Python tests above.

- [ ] **Step 3: Manual server boot check**

Run:

```bash
OPENAI_API_KEY=test HOME_ASSISTANT_URL=http://homeassistant.local:8123 HOME_ASSISTANT_TOKEN=test python -m linux_voice_assistant --frontend vape-server --debug
```

Expected: logs include:

```text
VAPE satellite server listening on ws://0.0.0.0:8765/vape
```

Stop with Ctrl-C after confirming startup.

- [ ] **Step 4: Final status**

Confirm:

- local frontend remains the default
- VAPE server starts without opening local microphone devices
- fake WebSocket client tests cover handshake, wake, mic audio routing, and playback downlink
- no VAPE device needs to be connected yet

---

## Scope Boundaries

This plan does not flash the VAPE device and does not implement the ESPHome external component. After this plan passes verification, the next plan should target custom VAPE firmware using the official `home-assistant-voice-pe` firmware as the base.
