#!/bin/bash
set -euo pipefail

CONFIG_PATH="${LVA_CONFIG:-/app/local/realtime-home-assistant.yaml}"

ARGS=()

if [ "${LVA_DEBUG:-0}" = "1" ] || [ "${ENABLE_DEBUG:-0}" = "1" ]; then
  ARGS+=("--debug")
fi

if [ -n "${LVA_NAME:-}" ]; then
  ARGS+=("--name" "${LVA_NAME}")
fi

if [ -n "${LVA_AUDIO_INPUT_DEVICE:-}" ]; then
  ARGS+=("--audio-input-device" "${LVA_AUDIO_INPUT_DEVICE}")
fi

if [ -n "${LVA_AUDIO_OUTPUT_DEVICE:-}" ]; then
  ARGS+=("--audio-output-device" "${LVA_AUDIO_OUTPUT_DEVICE}")
fi

if [ -n "${LVA_PREFERENCES_FILE:-}" ]; then
  ARGS+=("--preferences-file" "${LVA_PREFERENCES_FILE}")
fi

PULSE_COOKIE=${PULSE_COOKIE:-${XDG_RUNTIME_DIR:-/run/user/1000}/pulse/cookie}
if [ -n "${PULSE_COOKIE}" ] && [ ! -f "${PULSE_COOKIE}" ]; then
  mkdir -p "$(dirname "${PULSE_COOKIE}")"
  touch "${PULSE_COOKIE}"
  chmod 600 "${PULSE_COOKIE}"
fi

for _ in $(seq 1 20); do
  if pactl info >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! pactl info >/dev/null 2>&1; then
  echo "PulseAudio/PipeWire pulse server is not reachable" >&2
  exit 2
fi

exec ./script/run --config "${CONFIG_PATH}" "${ARGS[@]}" "$@"
