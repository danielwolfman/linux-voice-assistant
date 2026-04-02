# Realtime Satellite Architecture

## Overview

This fork turns `linux-voice-assistant` into a Linux-native OpenAI Realtime satellite.

The active voice path is now:

1. Local wake word detection on Linux.
2. Raw microphone audio streamed directly to OpenAI Realtime.
3. Streamed model audio played directly on Linux.
4. Home Assistant used only as a tool backend for entity lookup and service calls.

The old live Home Assistant Assist path is not used for STT, conversation, or TTS.

## Runtime Pieces

- `linux_voice_assistant/__main__.py`
  Loads config, wake-word models, audio devices, preferences, and starts the runtime.
- `linux_voice_assistant/runtime/controller.py`
  Owns the runtime state machine and session lifecycle.
- `linux_voice_assistant/realtime/client.py`
  Maintains the OpenAI Realtime session, streams audio, and handles tool calls.
- `linux_voice_assistant/ha_tools/client.py`
  Exposes a curated Home Assistant tool layer: `get_entities`, `get_state`, `call_service`.
- `linux_voice_assistant/audio/realtime_player.py`
  Plays streamed PCM audio from OpenAI with low latency.

## Session States

- `idle`
- `wake_detected`
- `session_starting`
- `streaming_input`
- `playing_output`
- `interrupted`
- `tool_call`
- `session_timeout`
- `back_to_idle`

## Conversation Flow

1. The mic thread runs constantly for wake word and stop word detection.
2. On wake word, the runtime opens or reuses an OpenAI Realtime session.
3. The local wake sound plays.
4. The runtime listens for speech, using a simple local silence gate to end the turn.
5. Audio is committed to OpenAI Realtime, which responds with streamed speech.
6. If the model needs Home Assistant state or control, it calls one of the curated HA tools.
7. After the spoken response, the runtime stays in follow-up listening mode until timeout.
8. On timeout, the Realtime session is closed and the runtime returns to wake-word idle mode.

## Interruption

- Repeating the wake word during playback cancels the active response and reopens listening.
- Saying the local stop word during an active session also cancels playback and returns to listening.
- Full duplex is not required; the runtime prioritizes quick cancellation and fast recovery.

## Home Assistant Boundary

The model does not receive raw Home Assistant internals.

It only sees curated tool responses built from:

- `/api/states`
- `/api/services/<domain>/<service>`
- `/api/websocket` registry lookups for areas and entity mapping
