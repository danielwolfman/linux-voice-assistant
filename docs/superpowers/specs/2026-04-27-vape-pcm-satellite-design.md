# VAPE PCM Satellite Design

## Goal

Use Home Assistant Voice Preview Edition (VAPE) as a thin voice frontend for the existing Linux OpenAI Realtime assistant. VAPE keeps local wake-word detection, microphone capture, speaker playback, button input, and LED feedback. The Linux box owns the Realtime session, Home Assistant tool bridge, conversation lifecycle, interruption behavior, logging, and deployment.

## Current Context

The `linux-voice-assistant` fork already contains the assistant brain:

- `linux_voice_assistant/realtime/client.py` opens OpenAI Realtime sessions, sends `input_audio_buffer.append`, commits turns, receives streamed PCM output, handles tool calls, and cancels responses.
- `linux_voice_assistant/runtime/controller.py` owns wake, listen, playback, interruption, follow-up, timeout, and Home Assistant activity logging.
- `linux_voice_assistant/ha_tools/client.py` exposes curated Home Assistant tools for entity search, state reads, and service calls.
- `linux_voice_assistant/__main__.py` currently wires this runtime to local Linux microphone capture and local Linux speaker playback.

The official VAPE firmware uses ESPHome `micro_wake_word` for local wake detection and `voice_assistant` for the stock Home Assistant Assist path. That stock active-session path must be replaced for this project.

## Architecture

The first implementation milestone adds a VAPE-facing satellite server to the Linux assistant. The Linux runtime is split into two roles:

1. Assistant brain
   - OpenAI Realtime client
   - session state machine
   - Home Assistant tools
   - interruption and timeout policy
   - activity logging

2. Audio frontend
   - local Linux mic/speaker frontend, existing behavior
   - remote VAPE satellite frontend, new behavior

The assistant brain should not know whether audio came from a local Linux microphone or a remote VAPE device. It should consume PCM input frames and emit PCM output frames through a small frontend interface.

## Protocol Choice

Use WebSocket for the first VAPE protocol.

Reasons:

- simple to implement and debug on Linux
- good enough for a trusted same-LAN deployment
- supports control JSON and binary audio frames on the same connection
- avoids Opus/WebRTC complexity while LAN bandwidth is not a concern
- preserves audio quality by sending lossless PCM frames

Do not use G.711 for this project unless forced by a later device constraint. OpenAI Realtime supports G.711, but it is lower quality than PCM and is mainly useful for telephony paths.

## Audio Format

The protocol must support lossless PCM16.

Preferred OpenAI-facing format:

- codec: `pcm_s16le`
- channels: mono
- sample rate: 24000 Hz
- frame duration: 20 ms or 40 ms

VAPE-facing protocol should explicitly negotiate sample rate:

- `pcm_s16le`, mono, 24000 Hz when the firmware can produce/play that cleanly
- `pcm_s16le`, mono, 48000 Hz when the VAPE audio pipeline is easier to drive at the hardware speaker rate

Linux remains responsible for final conversion into OpenAI Realtime's configured audio format. If VAPE sends 16000 Hz or 48000 Hz PCM, Linux resamples to 24000 Hz before forwarding to OpenAI. If OpenAI returns 24000 Hz PCM and VAPE playback expects 48000 Hz, Linux can either upsample before downlink or the firmware can resample locally. The initial implementation should centralize conversion on Linux for easier testing and logging.

Expected LAN bandwidth for 24 kHz mono PCM16 is about 384 kbps per direction before WebSocket overhead. This is acceptable for the target network.

## Control Messages

Control messages are UTF-8 JSON WebSocket text frames.

VAPE to Linux:

- `hello`: device id, firmware version, supported audio formats, capabilities
- `wake_detected`: wake word id/name and monotonic timestamp
- `audio_start`: selected upload format and frame duration
- `audio_stop`: local capture ended
- `button`: button action such as press, long_press, double_press
- `mute_changed`: hardware/software mute state
- `playback_done`: output queue drained
- `error`: device-side error code/message
- `ping`: keepalive

Linux to VAPE:

- `hello_ack`: selected audio formats and server protocol version
- `start_capture`: begin uplink audio after wake
- `stop_capture`: stop uplink audio and return to idle
- `start_playback`: prepare for downlink audio
- `stop_playback`: cancel queued speaker audio immediately
- `set_led`: listening, thinking, speaking, muted, error, idle
- `session_ended`: clean return to wake-word idle
- `error`: server-side error code/message
- `pong`: keepalive response

Binary WebSocket frames carry raw PCM audio. Direction is inferred from the connection state:

- VAPE to Linux while capture is active: microphone PCM
- Linux to VAPE while playback is active: assistant PCM

The first protocol version supports one active session per VAPE connection. Multi-device support can be built by running one frontend/controller instance per connected device later.

## Linux Runtime Changes

Add a frontend abstraction around the existing session controller:

- input path: frontend pushes microphone PCM frames into the session controller
- output path: session controller sends assistant PCM frames to the frontend instead of directly owning a local sounddevice player
- control path: session controller asks the frontend to stop playback, set LEDs, and return to idle

Keep the current local Linux audio path working. The existing local mode becomes one frontend implementation. The VAPE server becomes another frontend implementation.

The first implementation should keep VAD and turn commit logic on Linux. VAPE handles wake detection and then streams speech audio until Linux sends `stop_capture` or the session ends.

## VAPE Firmware Changes

Create custom ESPHome firmware based on the official VAPE firmware.

Keep:

- `micro_wake_word`
- I2S microphone configuration
- I2S speaker / mixer / resampler setup
- mute switch
- center button and rotary controls where practical
- LED phase scripts
- local wake sound if it does not harm capture timing

Replace:

- stock `voice_assistant.start`
- stock `voice_assistant.stop`
- stock Home Assistant Assist streaming during active sessions

With:

- custom satellite client component
- WebSocket connection to Linux server
- PCM uplink from microphone to Linux after wake detection
- PCM downlink from Linux to speaker playback
- immediate playback cancellation on `stop_playback`

The firmware milestone should start with a narrow external ESPHome component instead of trying to implement the audio transport in YAML only. YAML remains useful for wiring wake-word, button, mute, and LED events into the component.

## Interruption Behavior

Interruption is a first-class requirement.

During assistant playback:

1. VAPE continues local wake/stop detection where feasible.
2. If wake or stop is detected, VAPE sends `wake_detected` or `audio_stop`/button event to Linux.
3. Linux cancels the active OpenAI Realtime response.
4. Linux sends `stop_playback`.
5. VAPE clears speaker buffers immediately.
6. Linux sends `start_capture`.
7. VAPE resumes microphone streaming.

If full-duplex wake detection during playback proves unreliable, the first fallback is a physical button interruption path. The design must not depend on SSH or Linux processes running on the VAPE device.

## Error Handling

Linux should close or reset the session on:

- malformed protocol messages
- unsupported audio format negotiation
- VAPE disconnect
- OpenAI Realtime connection failure
- Home Assistant tool bridge failure that prevents useful operation
- playback drain timeout

VAPE should return to local wake-word idle on:

- Linux disconnect
- session timeout
- server `session_ended`
- Wi-Fi reconnect
- playback failure

Both sides should log protocol state changes with device id and session id.

## Testing

Linux-side tests:

- protocol message parsing and validation
- audio format negotiation
- PCM resampling between 16000, 24000, and 48000 Hz
- controller behavior when a remote frontend sends wake, audio frames, interruption, and disconnect
- output routing to a fake remote frontend

Manual Linux test before VAPE firmware:

- run a local WebSocket client that sends recorded PCM frames
- verify audio reaches OpenAI Realtime
- verify assistant PCM frames are received back over the WebSocket
- verify `stop_playback` clears queued output on interruption

Firmware-side tests:

- compile custom ESPHome firmware
- connect to Linux WebSocket server
- wake word sends `wake_detected`
- microphone frames arrive at Linux
- playback frames are audible through the VAPE speaker
- interruption cancels playback quickly

## Acceptance Criteria

The project is successful when:

1. The user says the wake word near VAPE.
2. VAPE wakes locally without idle audio streaming.
3. VAPE streams microphone PCM to the Linux box.
4. Linux runs the OpenAI Realtime assistant.
5. Linux uses Home Assistant only for tools, state, services, and automations.
6. Linux streams assistant PCM back to VAPE.
7. VAPE plays response audio promptly.
8. The user can interrupt the assistant and continue naturally.

## Non-Goals

- Running Linux processes on VAPE.
- Moving OpenAI Realtime session logic into ESP32 firmware.
- Keeping Home Assistant Assist as the live STT/conversation/TTS pipeline.
- Building WebRTC or Opus transport in the first milestone.
- Flashing VAPE before the Linux server and test client are working.
