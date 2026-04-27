# VAPE Cue Sounds Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add wake, idle, thinking/tool, and Realtime error cue sounds to the VAPE satellite flow.

**Architecture:** VAPE plays wake and idle sounds locally for immediate feedback. The Linux backend streams thinking/tool and OpenAI error cue audio over the existing VAPE PCM downlink because those states and assets belong to the assistant runtime.

**Tech Stack:** ESPHome YAML/audio_file, Python asyncio, aiohttp WebSocket PCM transport, ffmpeg for cue decoding, pytest.

---

### Task 1: Firmware Local Wake And Idle Cues

**Files:**
- Modify: `home-assistant-voice.yaml`

- [ ] Add `play_sound` before `id(vape_sat).start(...)` in both button and wake-word activation paths.
- [ ] Add `play_sound` for `mute_switch_on_sound` in `vape_satellite.on_idle`.
- [ ] Compile firmware with `.venv/bin/esphome compile config/vape-satellite-compile.yaml`.

### Task 2: Remote Cue Playback In Linux Backend

**Files:**
- Modify: `linux_voice_assistant/vape/server.py`
- Modify: `linux_voice_assistant/runtime/controller.py`
- Modify: `linux_voice_assistant/__main__.py`
- Test: `tests/test_satellite_server.py`
- Test: `tests/test_runtime_controller.py`

- [ ] Write failing tests for remote cue playback, cue state methods, and VAPE config preserving cue sound paths.
- [ ] Add a `play_file()` remote sink method that decodes cue files to `pcm_s16le` 24 kHz mono with `ffmpeg` and streams through the VAPE downlink.
- [ ] Make controller cue playback use the remote sink when available, including looped tool-call cue playback and OpenAI error sound playback.
- [ ] Keep VAPE prompt sounds enabled in config so the backend can stream the committed assets.
- [ ] Run `.venv/bin/pytest tests/test_satellite_server.py tests/test_runtime_controller.py -q`.

### Task 3: Verify End To End

**Files:**
- Runtime only

- [ ] Restart the VAPE backend server.
- [ ] Upload the compiled firmware over `/dev/ttyACM0`.
- [ ] Confirm `Hey Jarvis` wake detection, backend connection, and no old Docker assistant restart.
