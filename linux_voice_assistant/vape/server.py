"""WebSocket server for VAPE PCM satellite clients."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Coroutine, Optional

from aiohttp import WSMsgType, web
from aiohttp.client_exceptions import ClientConnectionResetError

from ..audio.pcm import PcmFormat, resample_pcm16_mono
from ..tools.codex_agent import CodexJob
from ..tools.timer import TimerRecord, format_timer_finished_notification
from .protocol import ProtocolError, build_control, negotiate_audio_format, parse_control

_LOGGER = logging.getLogger(__name__)

SendJson = Callable[[dict], Coroutine[Any, Any, None]]
SendBinary = Callable[[bytes], Coroutine[Any, Any, None]]
SessionStartedCallback = Callable[[str, object], None]
SessionClosedCallback = Callable[[str, object], Coroutine[Any, Any, None]]
SessionActivityCallback = Callable[[str], None]


@dataclass(frozen=True)
class RemoteWakeWord:
    id: str
    wake_word: str


@dataclass(frozen=True)
class VoiceNotification:
    text: str
    cue_sound: Optional[str] = None


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
        self._playing_until = 0.0
        self._started = False
        self._closed = False
        self._file_task: Optional[asyncio.Task[None]] = None

    @property
    def is_playing(self) -> bool:
        return not self._closed and time.monotonic() < self._playing_until

    @property
    def pending_samples(self) -> int:
        remaining_seconds = max(0.0, self._playing_until - time.monotonic())
        return int(remaining_seconds * self._output_sample_rate)

    def set_volume(self, volume: float) -> None:
        del volume

    def add_data(self, data: bytes) -> None:
        if self._closed or not data:
            return
        asyncio.create_task(self._send_audio(data))

    def play_file(self, path: str, done_callback: Optional[Callable[[], None]] = None) -> None:
        if self._closed:
            return
        if self._file_task is not None:
            self._file_task.cancel()
            self._file_task = None
        if self._started or self.is_playing:
            self.stop()
        self._file_task = asyncio.create_task(self._send_file_audio(Path(path), done_callback))

    def stop_file(self) -> None:
        if self._file_task is not None:
            self._file_task.cancel()
            self._file_task = None
        self.stop()

    def stop(self) -> None:
        if self._file_task is not None:
            self._file_task.cancel()
            self._file_task = None
        self._playing_until = 0.0
        self._started = False
        if not self._closed:
            asyncio.create_task(self._send_json(build_control("stop_playback")))

    def close(self) -> None:
        self._closed = True
        self.stop()

    def set_remote_state(self, state: str) -> None:
        if not self._closed:
            asyncio.create_task(self._send_json(build_control("set_state", state=state)))

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
        await self._send_binary(output)
        samples = len(output) // 2
        now = time.monotonic()
        self._playing_until = max(now, self._playing_until) + (samples / self._output_sample_rate)

    async def _send_file_audio(self, path: Path, done_callback: Optional[Callable[[], None]]) -> None:
        completed = False
        try:
            process = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(path),
                "-f",
                "s16le",
                "-acodec",
                "pcm_s16le",
                "-ac",
                "1",
                "-ar",
                "24000",
                "pipe:1",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()
            if process.returncode != 0:
                _LOGGER.warning("Failed to decode cue sound %s: %s", path, stderr.decode(errors="replace").strip())
                completed = True
                return
            for offset in range(0, len(stdout), 4096):
                if self._closed:
                    return
                await self._send_audio(stdout[offset : offset + 4096])
            await self._wait_for_playback_time()
            completed = True
        except asyncio.CancelledError:
            raise
        except FileNotFoundError:
            _LOGGER.warning("ffmpeg is required to play cue sound files")
            completed = True
        finally:
            if self._file_task is asyncio.current_task():
                self._file_task = None
            if completed and not self._closed and done_callback is not None:
                done_callback()

    async def _wait_for_playback_time(self) -> None:
        while not self._closed:
            remaining = self._playing_until - time.monotonic()
            if remaining <= 0:
                return
            await asyncio.sleep(min(0.05, remaining))


class SatelliteSessionHandler:
    def __init__(self, controller, *, session_id: str = "", on_activity: Optional[SessionActivityCallback] = None) -> None:
        self._controller = controller
        self.session_id = session_id
        self._on_activity = on_activity

    @property
    def controller(self):
        return self._controller

    async def handle_control(self, raw_message: str, send_json: SendJson) -> None:
        message = parse_control(raw_message)
        if message.type == "wake_detected":
            self._mark_activity()
            wake_word = str(message.payload.get("wake_word") or "wake")
            _LOGGER.info("VAPE wake detected: %s", wake_word)
            self._controller.wakeup(RemoteWakeWord(id=wake_word, wake_word=wake_word))
            await send_json(build_control("start_capture"))
            return
        if message.type == "audio_stop":
            self._mark_activity()
            self._controller.stop()
            return
        if message.type in {"ping", "playback_done", "mute_changed", "button"}:
            if message.type == "button":
                self._mark_activity()
            if message.type == "ping":
                await send_json(build_control("pong"))
            return
        raise ProtocolError(f"Unsupported control message: {message.type}")

    def handle_audio(self, audio_chunk: bytes) -> None:
        self._mark_activity()
        self._controller.handle_audio(audio_chunk)

    async def close(self) -> None:
        shutdown = getattr(self._controller, "shutdown", None)
        if callable(shutdown):
            await shutdown()

    def _mark_activity(self) -> None:
        if self._on_activity is not None and self.session_id:
            self._on_activity(self.session_id)


SessionFactory = Callable[[PcmFormat, SendJson, SendBinary, str], SatelliteSessionHandler]


def create_session_factory(
    make_controller: Callable[[RemotePlaybackSink, PcmFormat, str], object],
    *,
    output_sample_rate: int,
    on_session_started: Optional[SessionStartedCallback] = None,
    on_session_activity: Optional[SessionActivityCallback] = None,
) -> SessionFactory:
    def factory(selected_format: PcmFormat, send_json: SendJson, send_binary: SendBinary, session_id: str) -> SatelliteSessionHandler:
        sink = RemotePlaybackSink(
            selected_input_format=selected_format,
            output_sample_rate=output_sample_rate,
            send_json=send_json,
            send_binary=send_binary,
        )
        controller = make_controller(sink, selected_format, session_id)
        if on_session_started is not None:
            on_session_started(session_id, controller)
        return SatelliteSessionHandler(controller, session_id=session_id, on_activity=on_session_activity)

    return factory


class VoiceSessionRegistry:
    def __init__(self) -> None:
        self._sessions: dict[str, object] = {}
        self._last_active_session_id: Optional[str] = None
        self._pending_notifications: list[VoiceNotification] = []

    def register(self, session_id: str, controller: object) -> None:
        self._sessions[session_id] = controller
        self._last_active_session_id = session_id
        _LOGGER.debug("Registered VAPE voice session %s", session_id)
        if self._pending_notifications:
            notifications = list(self._pending_notifications)
            self._pending_notifications.clear()
            for notification in notifications:
                asyncio.create_task(self._deliver_when_idle(session_id, notification))

    async def unregister(self, session_id: str, controller: object) -> None:
        if self._sessions.get(session_id) is controller:
            self._sessions.pop(session_id, None)
        if self._last_active_session_id == session_id:
            self._last_active_session_id = next(reversed(self._sessions), None) if self._sessions else None
        _LOGGER.debug("Unregistered VAPE voice session %s", session_id)
        shutdown = getattr(controller, "shutdown", None)
        if callable(shutdown):
            await shutdown()

    def mark_active(self, session_id: str) -> None:
        if session_id in self._sessions:
            self._last_active_session_id = session_id

    async def notify_codex_job_finished(self, job: CodexJob) -> None:
        notification = VoiceNotification(format_codex_completion_notification(job))
        target_session_id = self._select_target_session(job.origin_session_id)
        if target_session_id is None:
            self._pending_notifications.append(notification)
            _LOGGER.info("Queued Codex completion notification; no VAPE clients are connected")
            return
        asyncio.create_task(self._deliver_when_idle(target_session_id, notification))

    async def notify_timer_finished(self, timer: TimerRecord) -> None:
        notification = VoiceNotification(format_timer_finished_notification(timer), cue_sound=timer.finished_sound)
        target_session_id = self._select_target_session(timer.origin_session_id)
        if target_session_id is None:
            self._pending_notifications.append(notification)
            _LOGGER.info("Queued timer completion notification; no VAPE clients are connected")
            return
        asyncio.create_task(self._deliver_when_idle(target_session_id, notification))

    def _select_target_session(self, origin_session_id: Optional[str]) -> Optional[str]:
        if origin_session_id and origin_session_id in self._sessions:
            return origin_session_id
        if self._last_active_session_id and self._last_active_session_id in self._sessions:
            return self._last_active_session_id
        if self._sessions:
            return next(reversed(self._sessions))
        return None

    async def _deliver_when_idle(self, session_id: str, notification: VoiceNotification, *, timeout_seconds: float = 1800.0) -> None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            controller = self._sessions.get(session_id)
            if controller is None:
                fallback_session_id = self._select_target_session(None)
                if fallback_session_id is None:
                    self._pending_notifications.append(notification)
                    return
                session_id = fallback_session_id
                continue
            can_accept = getattr(controller, "can_accept_notification", None)
            speak = getattr(controller, "speak_notification", None)
            if callable(can_accept) and callable(speak) and can_accept():
                delivered = await speak(notification.text, cue_sound=notification.cue_sound)
                if delivered:
                    return
            await asyncio.sleep(0.5)
        self._pending_notifications.append(notification)
        _LOGGER.warning("Timed out waiting for an idle VAPE session; Codex notification queued")


def format_codex_completion_notification(job: CodexJob) -> str:
    if job.status == "succeeded":
        result = job.final_output.strip() or "Codex finished without a final message."
        return f"Codex finished job {job.id}. Summarize this result for speech: {result}"
    detail = job.error or job.final_output or job.last_event or "No details were reported."
    return f"Codex job {job.id} did not finish successfully. Status: {job.status}. Details: {detail}"


def create_app(session_factory: SessionFactory, *, path: str = "/vape", on_session_closed: Optional[SessionClosedCallback] = None) -> web.Application:
    app = web.Application()

    async def websocket_handler(request: web.Request) -> web.WebSocketResponse:
        websocket = web.WebSocketResponse()
        await websocket.prepare(request)
        peer = request.remote or "unknown"
        _LOGGER.info("VAPE client connected from %s", peer)

        async def send_json(payload: dict) -> None:
            try:
                await websocket.send_str(json.dumps(payload, separators=(",", ":")))
            except ClientConnectionResetError:
                _LOGGER.debug("Dropped control frame for closing VAPE client: %s", payload.get("type"))

        async def send_binary(payload: bytes) -> None:
            await websocket.send_bytes(payload)

        selected_format: Optional[PcmFormat] = None
        handler: Optional[SatelliteSessionHandler] = None
        received_audio = False
        session_id = ""

        try:
            async for ws_message in websocket:
                if ws_message.type == WSMsgType.TEXT:
                    control = parse_control(ws_message.data)
                    if control.type == "hello":
                        selected_format = negotiate_audio_format(control)
                        device_id = str(control.payload.get("device_id") or peer or "client")
                        session_id = f"{device_id}-{int(time.time() * 1000)}"
                        handler = session_factory(selected_format, send_json, send_binary, session_id)
                        _LOGGER.info(
                            "VAPE client negotiated %s/%s/%s",
                            selected_format.codec,
                            selected_format.sample_rate,
                            selected_format.channels,
                        )
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
                    if not received_audio:
                        _LOGGER.info("VAPE audio uplink started from %s", peer)
                        received_audio = True
                    handler.handle_audio(ws_message.data)
                elif ws_message.type == WSMsgType.ERROR:
                    _LOGGER.warning("VAPE WebSocket error: %s", websocket.exception())
        except ProtocolError as err:
            await websocket.send_json(build_control("error", code="protocol_error", message=str(err)))
            await websocket.close()
        finally:
            _LOGGER.info("VAPE client disconnected from %s", peer)
            if handler is not None:
                if on_session_closed is not None:
                    await on_session_closed(handler.session_id, handler.controller)
                else:
                    await handler.close()

        return websocket

    app.router.add_get(path, websocket_handler)
    return app
