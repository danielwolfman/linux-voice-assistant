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
