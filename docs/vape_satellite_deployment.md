# VAPE Satellite Deployment

This document describes how to run the Linux OpenAI Realtime backend for custom Home Assistant Voice Preview Edition firmware.

The intended architecture is:

- VAPE: local `Hey Jarvis` wake word, microphone capture, speaker playback, LEDs, button
- Linux server: OpenAI Realtime session, Home Assistant tools, web search, interruption, timeout, cue sounds, logs
- Home Assistant: entity state and service execution backend only

Home Assistant Assist STT, conversation, and TTS are not used during an active VAPE session.

## Network Requirements

The VAPE and Linux server must be on the same LAN, and the VAPE must be able to reach:

```text
ws://<linux-server-ip>:8765/vape
```

For a permanent install, give the Linux server a DHCP reservation or static IP and compile the VAPE firmware with that IP in `vape_satellite_url`.

## Server Dependencies

Install system packages:

```sh
sudo apt-get update
sudo apt-get install -y ffmpeg libmpv-dev libasound2-dev libportaudio2 python3-venv python3-dev
```

`ffmpeg` is required for cue sounds streamed to VAPE, including `processing.wav`, `tool_call_processing.wav`, and OpenAI error MP3s.

## Checkout And Python Setup

```sh
git clone https://github.com/danielwolfman/linux-voice-assistant.git
cd linux-voice-assistant
python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -e ".[dev]"
```

For a production host, `.[dev]` can be replaced with `.` if test tooling is not needed.

## Configuration

Create a local config file, for example `local/realtime-home-assistant.yaml`:

```yaml
device:
  name: Mycroft
  preferences_file: local/preferences.json

audio:
  input_device:
  output_device:
  input_block_size: 1024
  wakeup_sound: sounds/wake_word_triggered.flac
  processing_sound: sounds/processing.wav
  tool_call_sound: sounds/tool_call_processing.wav
  session_end_sound: sounds/mute_switch_on.flac
  wake_word_dirs:
    - wakewords

wakeword:
  model: hey_jarvis
  stop_model: stop
  download_dir: local
  refractory_seconds: 2.0

openai:
  model: gpt-realtime
  voice: coral
  instructions: >-
    You are Mycroft. Keep spoken replies short and natural.
    Use Home Assistant tools for smart-home state and control.

home_assistant:
  url: http://homeassistant.local:8123
  verify_ssl: false

runtime:
  session_timeout_seconds: 20
  vad_threshold: 0.014
  min_speech_seconds: 0.2
  end_silence_seconds: 0.5
  follow_up_after_tool_call: false

tools:
  enable_get_entities: true
  enable_get_state: true
  enable_call_service: true
  enable_web_search: true
```

Keep secrets in environment variables or a local `.env` file that is not committed:

```sh
export OPENAI_API_KEY="..."
export HOME_ASSISTANT_URL="http://homeassistant.local:8123"
export HOME_ASSISTANT_TOKEN="..."
export LVA_HA_VERIFY_SSL="false"
```

## Run Manually

```sh
.venv/bin/python -m linux_voice_assistant \
  --config local/realtime-home-assistant.yaml \
  --frontend vape-server \
  --vape-server-host 0.0.0.0 \
  --vape-server-port 8765 \
  --vape-server-path /vape \
  --vape-output-sample-rate 48000 \
  --vad-threshold 0.014 \
  --end-silence-seconds 0.5
```

Expected startup log:

```text
VAPE satellite server listening on ws://0.0.0.0:8765/vape
```

When VAPE connects and wakes:

```text
VAPE client connected from <vape-ip>
VAPE client negotiated pcm_s16le/16000/1
VAPE wake detected: Hey Jarvis
VAPE audio uplink started from <vape-ip>
```

## Run With systemd

Create `/etc/systemd/system/vape-satellite-backend.service`:

```ini
[Unit]
Description=VAPE OpenAI Realtime Backend
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=daniel
WorkingDirectory=/opt/linux-voice-assistant
Environment=OPENAI_API_KEY=replace-with-secret-or-use-env-file
Environment=HOME_ASSISTANT_URL=http://homeassistant.local:8123
Environment=HOME_ASSISTANT_TOKEN=replace-with-secret-or-use-env-file
Environment=LVA_HA_VERIFY_SSL=false
ExecStart=/opt/linux-voice-assistant/.venv/bin/python -m linux_voice_assistant --config local/realtime-home-assistant.yaml --frontend vape-server --vape-server-host 0.0.0.0 --vape-server-port 8765 --vape-server-path /vape --vape-output-sample-rate 48000 --vad-threshold 0.014 --end-silence-seconds 0.5
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
```

Prefer an `EnvironmentFile=` with root-only permissions for real secrets:

```ini
EnvironmentFile=/etc/vape-satellite-backend.env
```

Then enable it:

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now vape-satellite-backend
sudo systemctl status vape-satellite-backend
journalctl -u vape-satellite-backend -f
```

## Docker Option

Docker is still useful for a local Linux microphone/speaker satellite, but the VAPE backend does not need host audio devices. If packaging this mode in Docker, expose port `8765` and keep `ffmpeg`, sound assets, and the local config mounted in the container.

The previous local Docker assistant on the temporary machine was disabled because VAPE is now the wake/mic/speaker device.

## Deploy Firmware To Another VAPE

Use the firmware repository and follow `docs/custom-vape-satellite-firmware.md` there.

The important value to set per deployment is:

```yaml
substitutions:
  vape_satellite_url: "ws://<linux-server-ip>:8765/vape"
```

Flash each VAPE over USB-C with ESPHome. Because firmware uses `name_add_mac_suffix: true`, multiple devices can be flashed from the same base YAML and still get unique ESPHome names.

## Validation Checklist

1. Backend service is listening on `0.0.0.0:8765`.
2. VAPE serial logs show Wi-Fi connected and micro wake word detection running.
3. Say `Hey Jarvis`.
4. VAPE plays the local wake cue.
5. Backend logs `VAPE wake detected: Hey Jarvis`.
6. Backend logs Realtime response activity.
7. VAPE plays streamed assistant audio.
8. Home Assistant tool and `web_search` calls play the thinking cue while running.
9. Session timeout or end-session returns VAPE to idle and plays the idle cue.
