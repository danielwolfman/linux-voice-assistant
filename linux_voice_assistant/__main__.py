#!/usr/bin/env python3
"""Linux-native OpenAI Realtime Home Assistant satellite."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import threading
import time
from pathlib import Path
from queue import Queue
from typing import Dict, List, Optional, Set, Union

import numpy as np
import soundcard as sc
import sounddevice as sd  # type: ignore[import-untyped]
from getmac import get_mac_address  # type: ignore
from pymicro_wakeword import MicroWakeWord, MicroWakeWordFeatures
from pyopen_wakeword import OpenWakeWord, OpenWakeWordFeatures

from .config import AppConfig, load_config
from .models import AvailableWakeWord, Preferences, ServerState, WakeWordType
from .mpv_player import MpvMediaPlayer
from .runtime.controller import SessionController
from .util import get_default_interface, get_version

_LOGGER = logging.getLogger(__name__)


async def main() -> None:
    config, args = load_config()

    if args.list_input_devices:
        print("Audio input devices:")
        print("=" * 19)
        for idx, mic in enumerate(sc.all_microphones()):
            print(f"[{idx}] {mic.name}")
        return

    if args.list_output_devices:
        print("Audio output devices:")
        print("=" * 20)
        for idx, device in enumerate(sd.query_devices()):
            if device.get("max_output_channels", 0) > 0:
                print(f"[{idx}] {device['name']}")
        return

    logging.basicConfig(level=logging.DEBUG if config.debug else logging.INFO)
    _LOGGER.debug("Loaded config: %s", config)
    logging.getLogger("openai.resources.realtime.realtime").setLevel(logging.INFO)
    logging.getLogger("websockets.client").setLevel(logging.INFO)

    network_interface = get_default_interface() or "unknown"
    mac_address = get_mac_address(interface=network_interface) or get_mac_address() or "00:00:00:00:00:00"
    mac_address_clean = mac_address.replace(":", "").lower()

    friendly_name = config.name or f"LVA Realtime - {mac_address_clean}"
    device_name = f"lva-realtime-{mac_address_clean}"

    version = get_version()
    _LOGGER.info("Starting %s (%s)", friendly_name, version)

    config.download_dir.mkdir(parents=True, exist_ok=True)
    preferences_path = config.preferences_file
    preferences = _load_preferences(preferences_path)
    initial_volume = preferences.volume if preferences.volume is not None else 1.0
    initial_volume = max(0.0, min(1.0, float(initial_volume)))
    preferences.volume = initial_volume

    mic = _resolve_microphone(config.audio_input_device)

    available_wake_words = _load_available_wake_words(config)
    wake_models, active_wake_words = _load_wake_models(config, preferences, available_wake_words)
    stop_model = _load_stop_model(config)

    state = ServerState(
        name=device_name,
        friendly_name=friendly_name,
        network_interface=network_interface,
        mac_address=mac_address,
        ip_address="127.0.0.1",
        version=version,
        esphome_version="realtime",
        audio_queue=Queue(),
        entities=[],
        available_wake_words=available_wake_words,
        wake_words=wake_models,
        active_wake_words=active_wake_words,
        stop_word=stop_model,
        music_player=MpvMediaPlayer(),
        tts_player=MpvMediaPlayer(),
        wakeup_sound=config.wakeup_sound or "",
        timer_finished_sound="",
        processing_sound=config.processing_sound or "",
        mute_sound="",
        unmute_sound="",
        preferences=preferences,
        preferences_path=preferences_path,
        download_dir=config.download_dir,
        refractory_seconds=config.refractory_seconds,
        output_only=False,
        volume=initial_volume,
        timer_max_ring_seconds=0.0,
    )

    initial_volume_percent = int(round(initial_volume * 100))
    state.music_player.set_volume(initial_volume_percent)
    state.tts_player.set_volume(initial_volume_percent)

    loop = asyncio.get_running_loop()
    controller = SessionController(state=state, config=config, loop=loop)
    state.satellite = controller

    process_audio_thread = threading.Thread(
        target=process_audio,
        args=(state, mic, config.audio_input_block_size),
        daemon=True,
    )
    process_audio_thread.start()

    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        state.audio_queue.put_nowait(None)
        await controller.shutdown()
        process_audio_thread.join()


def _resolve_microphone(audio_input_device: Optional[str]):
    if audio_input_device is None:
        return sc.default_microphone()

    try:
        return sc.get_microphone(int(audio_input_device))
    except ValueError:
        return sc.get_microphone(audio_input_device)


def _load_preferences(preferences_path: Path) -> Preferences:
    if preferences_path.exists():
        with open(preferences_path, "r", encoding="utf-8") as preferences_file:
            return Preferences(**json.load(preferences_file))
    return Preferences()


def _load_available_wake_words(config: AppConfig) -> Dict[str, AvailableWakeWord]:
    wake_word_dirs = list(config.wake_word_dirs)
    wake_word_dirs.append(config.download_dir / "external_wake_words")
    available_wake_words: Dict[str, AvailableWakeWord] = {}

    for wake_word_dir in wake_word_dirs:
        for model_config_path in wake_word_dir.glob("*.json"):
            model_id = model_config_path.stem
            if model_id == config.stop_model:
                continue

            with open(model_config_path, "r", encoding="utf-8") as model_config_file:
                model_config = json.load(model_config_file)
                model_type = WakeWordType(model_config["type"])
                wake_word_path = model_config_path.parent / model_config["model"] if model_type == WakeWordType.OPEN_WAKE_WORD else model_config_path
                available_wake_words[model_id] = AvailableWakeWord(
                    id=model_id,
                    type=model_type,
                    wake_word=model_config["wake_word"],
                    trained_languages=model_config.get("trained_languages", []),
                    wake_word_path=wake_word_path,
                )

    return available_wake_words


def _load_wake_models(config: AppConfig, preferences: Preferences, available_wake_words: Dict[str, AvailableWakeWord]) -> tuple[Dict[str, Union[MicroWakeWord, OpenWakeWord]], Set[str]]:
    active_wake_words: Set[str] = set()
    wake_models: Dict[str, Union[MicroWakeWord, OpenWakeWord]] = {}

    if preferences.active_wake_words:
        for wake_word_id in preferences.active_wake_words:
            wake_word = available_wake_words.get(wake_word_id)
            if wake_word is None:
                _LOGGER.warning("Unrecognized wake word id: %s", wake_word_id)
                continue
            wake_models[wake_word_id] = wake_word.load()
            active_wake_words.add(wake_word_id)

    if not wake_models:
        wake_word = available_wake_words[config.wake_model]
        wake_models[config.wake_model] = wake_word.load()
        active_wake_words.add(config.wake_model)

    return wake_models, active_wake_words


def _load_stop_model(config: AppConfig) -> MicroWakeWord:
    for wake_word_dir in list(config.wake_word_dirs) + [config.download_dir / "external_wake_words"]:
        stop_config_path = wake_word_dir / f"{config.stop_model}.json"
        if stop_config_path.exists():
            return MicroWakeWord.from_config(stop_config_path)
    raise FileNotFoundError(f"Unable to find stop model {config.stop_model}")


def process_audio(state: ServerState, mic, block_size: int):
    """Process audio chunks from the microphone."""

    wake_words: List[Union[MicroWakeWord, OpenWakeWord]] = []
    micro_features: Optional[MicroWakeWordFeatures] = None
    micro_inputs: List[np.ndarray] = []

    oww_features: Optional[OpenWakeWordFeatures] = None
    oww_inputs: List[np.ndarray] = []
    has_oww = False

    last_active: Optional[float] = None

    try:
        _LOGGER.debug("Opening audio input device: %s", mic.name)
        with mic.recorder(samplerate=16000, channels=1, blocksize=block_size) as mic_in:
            while True:
                audio_chunk_array = mic_in.record(block_size).reshape(-1)
                audio_chunk = (np.clip(audio_chunk_array, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()

                if state.satellite is None:
                    continue

                if state.satellite.is_microphone_blocked():
                    continue

                if (not wake_words) or (state.wake_words_changed and state.wake_words):
                    state.wake_words_changed = False
                    wake_words = [ww for ww in state.wake_words.values() if ww.id in state.active_wake_words]
                    has_oww = any(isinstance(wake_word, OpenWakeWord) for wake_word in wake_words)

                    if micro_features is None:
                        micro_features = MicroWakeWordFeatures()

                    if has_oww and oww_features is None:
                        oww_features = OpenWakeWordFeatures.from_builtin()

                try:
                    state.satellite.handle_audio(audio_chunk)

                    assert micro_features is not None
                    micro_inputs.clear()
                    micro_inputs.extend(micro_features.process_streaming(audio_chunk))

                    if has_oww:
                        assert oww_features is not None
                        oww_inputs.clear()
                        oww_inputs.extend(oww_features.process_streaming(audio_chunk))

                    for wake_word in wake_words:
                        activated = False
                        if isinstance(wake_word, MicroWakeWord):
                            for micro_input in micro_inputs:
                                if wake_word.process_streaming(micro_input):
                                    activated = True
                        elif isinstance(wake_word, OpenWakeWord):
                            for oww_input in oww_inputs:
                                for prob in wake_word.process_streaming(oww_input):
                                    if prob > 0.5:
                                        activated = True

                        if activated and not state.muted:
                            now = time.monotonic()
                            if (last_active is None) or ((now - last_active) > state.refractory_seconds):
                                state.satellite.wakeup(wake_word)
                                last_active = now

                    stopped = False
                    for micro_input in micro_inputs:
                        if state.stop_word.process_streaming(micro_input):
                            stopped = True

                    if stopped and (state.stop_word.id in state.active_wake_words) and not state.muted:
                        _LOGGER.debug("Stop word detected")
                        state.satellite.stop()
                except Exception:
                    _LOGGER.exception("Unexpected error handling audio")
    except Exception:
        _LOGGER.exception("Failed to process microphone audio")


def run() -> None:
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    run()
