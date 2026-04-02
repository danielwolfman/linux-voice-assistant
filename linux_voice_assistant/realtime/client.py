"""OpenAI Realtime client wrapper."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Optional, cast

import numpy as np
from openai import AsyncOpenAI

from ..ha_tools.client import HomeAssistantToolBridge

_LOGGER = logging.getLogger(__name__)


class OpenAIRealtimeClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        voice: str,
        instructions: str,
        tools: HomeAssistantToolBridge,
        api_base: Optional[str] = None,
        on_audio_delta: Callable[[bytes], Awaitable[None]],
        on_response_created: Callable[[str], Awaitable[None]],
        on_response_done: Callable[[str, str, dict[str, int], str], Awaitable[None]],
        on_tool_call_started: Callable[[str], Awaitable[None]],
        on_tool_call_finished: Callable[[str], Awaitable[None]],
        on_end_session_requested: Callable[[str], Awaitable[None]],
    ) -> None:
        self._client = AsyncOpenAI(api_key=api_key, base_url=api_base)
        self._model = model
        self._voice = voice
        self._instructions = instructions
        self._tools = tools
        self._on_audio_delta = on_audio_delta
        self._on_response_created = on_response_created
        self._on_response_done = on_response_done
        self._on_tool_call_started = on_tool_call_started
        self._on_tool_call_finished = on_tool_call_finished
        self._on_end_session_requested = on_end_session_requested
        self._connection: Optional[Any] = None
        self._connection_context: Optional[Any] = None
        self._reader_task: Optional[asyncio.Task[None]] = None
        self._current_response_id: Optional[str] = None
        self._discarded_response_ids: set[str] = set()
        self._latest_assistant_transcript = ""

    async def connect(self) -> None:
        if self._connection is not None:
            return

        self._connection_context = self._client.realtime.connect(model=self._model)
        self._connection = await self._connection_context.__aenter__()
        session_config: Any = {
            "type": "realtime",
            "model": self._model,
            "instructions": self._instructions,
            "output_modalities": ["audio"],
            "audio": {
                "output": {
                    "voice": self._voice,
                    "format": {"type": "audio/pcm", "rate": 24000},
                },
                "input": {
                    "format": {"type": "audio/pcm", "rate": 24000},
                    "transcription": {"model": "gpt-4o-mini-transcribe"},
                    "turn_detection": None,
                },
            },
            "tools": self._tools.tool_definitions() + [_end_session_tool_definition()],
            "tool_choice": "auto",
        }
        await self._connection.session.update(session=session_config)
        self._reader_task = asyncio.create_task(self._read_events())

    async def append_input_audio(self, audio_chunk: bytes) -> None:
        await self.connect()
        assert self._connection is not None
        realtime_chunk = resample_pcm16_mono(audio_chunk, source_rate=16000, target_rate=24000)
        await self._connection.send(
            {
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(realtime_chunk).decode("utf-8"),
            }
        )

    async def commit_turn(self) -> None:
        if self._connection is None:
            return
        await self._connection.send({"type": "input_audio_buffer.commit"})
        await self._connection.send({"type": "response.create"})

    async def clear_input_audio(self) -> None:
        if self._connection is None:
            return
        await self._connection.send({"type": "input_audio_buffer.clear"})

    async def cancel_response(self) -> None:
        if self._connection is None:
            return
        if self._current_response_id:
            self._discarded_response_ids.add(self._current_response_id)
        await self._connection.send({"type": "response.cancel"})

    async def close(self) -> None:
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None

        if self._connection_context is not None:
            await self._connection_context.__aexit__(None, None, None)
        self._connection = None
        self._connection_context = None
        self._current_response_id = None
        self._discarded_response_ids.clear()

    async def _read_events(self) -> None:
        assert self._connection is not None
        try:
            async for event in self._connection:
                event_type = getattr(event, "type", "unknown")

                if event_type == "response.created":
                    response_id = getattr(event.response, "id", None)
                    if response_id:
                        self._latest_assistant_transcript = ""
                        self._current_response_id = response_id
                        await self._on_response_created(response_id)
                    continue

                if event_type == "response.output_audio.delta":
                    response_id = getattr(event, "response_id", None)
                    if response_id and response_id in self._discarded_response_ids:
                        continue
                    await self._on_audio_delta(base64.b64decode(event.delta))
                    continue

                if event_type == "conversation.item.input_audio_transcription.completed":
                    transcript = str(getattr(event, "transcript", "") or "").strip()
                    if transcript:
                        _LOGGER.debug("User transcript: %s", transcript)
                    continue

                if event_type == "conversation.item.done":
                    transcript = _extract_assistant_transcript(getattr(event, "item", None))
                    if transcript:
                        self._latest_assistant_transcript = transcript
                        _LOGGER.debug("Assistant transcript: %s", transcript)
                    continue

                if event_type == "response.function_call_arguments.done":
                    await self._handle_tool_call(event)
                    continue

                if event_type == "response.done":
                    response = getattr(event, "response", None)
                    response_id = str(getattr(response, "id", "") or "")
                    status = str(getattr(response, "status", "unknown") or "unknown")
                    usage = _summarize_usage(getattr(response, "usage", None))
                    transcript = self._latest_assistant_transcript
                    self._discarded_response_ids.discard(response_id)
                    if response_id == self._current_response_id:
                        self._current_response_id = None
                    await self._on_response_done(response_id, status, usage, transcript)
                    continue

                if event_type == "error":
                    _LOGGER.error("Realtime error: %s", event)
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception("Realtime event reader crashed")

    async def _handle_tool_call(self, event: Any) -> None:
        tool_name = str(getattr(event, "name", "unknown"))
        await self._on_tool_call_started(tool_name)
        try:
            arguments = json.loads(getattr(event, "arguments", "{}") or "{}")
        except json.JSONDecodeError:
            arguments = {}

        _LOGGER.debug("Realtime function call: %s args=%s", tool_name, arguments)

        if tool_name == "end_session":
            reason = str(arguments.get("reason", "user_requested_end"))
            await self._on_end_session_requested(reason)
            result = {"status": "ok", "reason": reason}
        else:
            result = await self._tools.execute_tool(tool_name, arguments)
        _LOGGER.debug("Realtime function result: %s result=%s", tool_name, result)
        assert self._connection is not None
        await self._connection.send(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": cast(str, getattr(event, "call_id", "")),
                    "output": json.dumps(result),
                },
            }
        )
        await self._connection.send({"type": "response.create"})
        await self._on_tool_call_finished(tool_name)


def _summarize_usage(usage: Any) -> dict[str, int]:
    if usage is None:
        return {}

    input_details = _lookup(usage, "input_token_details")
    output_details = _lookup(usage, "output_token_details")

    summary = {
        "input_tokens": _as_int(_lookup(usage, "input_tokens")),
        "output_tokens": _as_int(_lookup(usage, "output_tokens")),
        "total_tokens": _as_int(_lookup(usage, "total_tokens")),
        "cached_input_tokens": _as_int(_lookup(input_details, "cached_tokens")),
        "input_text_tokens": _as_int(_lookup(input_details, "text_tokens")),
        "input_audio_tokens": _as_int(_lookup(input_details, "audio_tokens")),
        "output_text_tokens": _as_int(_lookup(output_details, "text_tokens")),
        "output_audio_tokens": _as_int(_lookup(output_details, "audio_tokens")),
    }

    if summary["input_text_tokens"] == 0 and summary["input_audio_tokens"] == 0:
        summary["input_text_tokens"] = summary["input_tokens"]

    if summary["output_text_tokens"] == 0 and summary["output_audio_tokens"] == 0:
        summary["output_audio_tokens"] = summary["output_tokens"]

    return summary


def _lookup(value: Any, key: str) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _end_session_tool_definition() -> dict[str, Any]:
    return {
        "type": "function",
        "name": "end_session",
        "description": "End the active voice session after your reply. Use this when the user signals they are done, says goodbye, asks to stop listening, or clearly wants the conversation to end.",
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Short reason like done, goodbye, stop_listening, or task_completed.",
                }
            },
            "additionalProperties": False,
        },
    }


def _extract_assistant_transcript(item: Any) -> str:
    if item is None:
        return ""

    role = _lookup(item, "role")
    item_type = _lookup(item, "type")
    if role != "assistant" or item_type != "message":
        return ""

    content = _lookup(item, "content") or []
    transcripts: list[str] = []
    for part in content:
        part_type = _lookup(part, "type")
        if part_type == "output_audio":
            transcript = str(_lookup(part, "transcript") or "").strip()
            if transcript:
                transcripts.append(transcript)
        elif part_type == "output_text":
            text = str(_lookup(part, "text") or "").strip()
            if text:
                transcripts.append(text)
    return " ".join(transcripts).strip()


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
