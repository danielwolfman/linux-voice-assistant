"""Configuration loading for the Realtime Linux satellite."""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Sequence

import yaml  # type: ignore[import-untyped]

_MODULE_DIR = Path(__file__).parent
_REPO_DIR = _MODULE_DIR.parent
_WAKEWORDS_DIR = _REPO_DIR / "wakewords"
_SOUNDS_DIR = _REPO_DIR / "sounds"

DEFAULT_INSTRUCTIONS = (
    "You are Berta, a Linux voice satellite for Home Assistant. "
    "Speak naturally and keep spoken replies short. "
    "Use Home Assistant tools for smart-home state and control instead of guessing. "
    "When the user asks Codex or an agent to do software work, use the Codex tools. "
    "Codex tasks are asynchronous: acknowledge dispatch briefly, and use Codex status tools for follow-up questions about progress. "
    "Use the configured default Codex execution backend. Ask for explicit confirmation before running Codex outside the default sandbox/container. "
    "If the user does not specify a repo or workspace for Codex, omit the workspace field and use the configured default; do not ask which repo. "
    "When the user asks you to send text, links, or files to Discord, use the Discord tool. "
    "When the user asks to set a timer, use the timer tools; the assistant will notify the requesting device when the timer finishes. "
    "Ask a concise follow-up question when a device or area is ambiguous. "
    "Do not mention internal tool names, API calls, or hidden reasoning."
)


@dataclass(frozen=True)
class AppConfig:
    name: Optional[str]
    config_path: Optional[Path]
    audio_input_device: Optional[str]
    audio_input_block_size: int
    audio_output_device: Optional[str]
    wake_word_dirs: list[Path]
    wake_model: str
    stop_model: str
    download_dir: Path
    refractory_seconds: float
    wakeup_sound: Optional[str]
    processing_sound: Optional[str]
    tool_call_sound: Optional[str]
    session_end_sound: Optional[str]
    timer_finished_sound: Optional[str]
    preferences_file: Path
    debug: bool
    openai_api_key: str
    openai_model: str
    openai_voice: str
    openai_api_base: Optional[str]
    openai_instructions: str
    ha_url: str
    ha_token: str
    ha_verify_ssl: bool
    session_timeout_seconds: float
    vad_threshold: float
    min_speech_seconds: float
    end_silence_seconds: float
    follow_up_after_tool_call: bool
    memory_interactions_count: int
    enable_tool_get_entities: bool
    enable_tool_get_state: bool
    enable_tool_call_service: bool
    enable_tool_web_search: bool
    enable_tool_codex_agent: bool
    enable_tool_timer: bool
    enable_tool_discord: bool
    codex_jobs_dir: Path
    codex_workspace_dir: Path
    codex_docker_image: str
    codex_host_codex_home: Path
    codex_host_gh_config_dir: Path
    codex_host_command: str
    codex_dispatch_mode: str
    codex_app_server_command: str
    codex_app_server_url: str
    discord_enabled: bool
    discord_bot_token: str
    discord_client_id: str
    discord_allowed_user_ids: str
    frontend: str
    vape_server_host: str
    vape_server_port: int
    vape_server_path: str
    vape_output_sample_rate: int


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", help="Path to YAML configuration file")
    parser.add_argument("--name", help="Friendly name for the device")
    parser.add_argument("--audio-input-device", help="Microphone name or index")
    parser.add_argument("--list-input-devices", action="store_true", help="List audio input devices and exit")
    parser.add_argument("--audio-input-block-size", type=int, help="Audio input block size in samples")
    parser.add_argument("--audio-output-device", help="Output device name or index for streamed replies")
    parser.add_argument("--list-output-devices", action="store_true", help="List audio output devices and exit")
    parser.add_argument("--wake-word-dir", action="append", help="Directory with wake word models and configs")
    parser.add_argument("--wake-model", help="Default active wake model id")
    parser.add_argument("--stop-model", help="Stop wake model id")
    parser.add_argument("--download-dir", help="Directory for downloaded wake words")
    parser.add_argument("--refractory-seconds", type=float, help="Seconds before wake word can retrigger")
    parser.add_argument("--wakeup-sound", help="Sound file played after wake word detection")
    parser.add_argument("--processing-sound", help="Sound file played after the mic turn is committed")
    parser.add_argument("--tool-call-sound", help="Sound file looped while tool calls are in progress")
    parser.add_argument("--session-end-sound", help="Sound file played when the conversation ends")
    parser.add_argument("--timer-finished-sound", help="Sound file played before a finished timer notification")
    parser.add_argument("--preferences-file", help="Path to preferences JSON file")
    parser.add_argument("--openai-model", help="OpenAI Realtime model")
    parser.add_argument("--openai-voice", help="OpenAI voice name")
    parser.add_argument("--openai-api-base", help="Optional OpenAI API base URL")
    parser.add_argument("--openai-instructions", help="Realtime system instructions")
    parser.add_argument("--ha-url", help="Home Assistant base URL")
    parser.add_argument("--ha-token", help="Home Assistant long-lived access token")
    parser.add_argument("--ha-verify-ssl", action="store_true", help="Verify Home Assistant TLS certificates")
    parser.add_argument("--no-ha-verify-ssl", action="store_true", help="Disable Home Assistant TLS verification")
    parser.add_argument("--session-timeout-seconds", type=float, help="Idle seconds before returning to wake-word mode")
    parser.add_argument("--vad-threshold", type=float, help="RMS threshold used to start a turn")
    parser.add_argument("--min-speech-seconds", type=float, help="Minimum speech duration before ending a turn")
    parser.add_argument("--end-silence-seconds", type=float, help="Silence duration that ends a turn")
    parser.add_argument("--follow-up-after-tool-call", action="store_true", help="Keep listening after a tool-backed response")
    parser.add_argument("--no-follow-up-after-tool-call", action="store_true", help="Return to wake-word mode after a tool-backed response")
    parser.add_argument("--memory-interactions-count", type=int, help="Recent user/assistant interaction pairs loaded into context on wakeup")
    parser.add_argument("--enable-tool-codex-agent", action="store_true", help="Enable Codex agent tools")
    parser.add_argument("--disable-tool-codex-agent", action="store_true", help="Disable Codex agent tools")
    parser.add_argument("--enable-tool-timer", action="store_true", help="Enable timer tools")
    parser.add_argument("--disable-tool-timer", action="store_true", help="Disable timer tools")
    parser.add_argument("--enable-tool-discord", action="store_true", help="Enable Discord messaging tools")
    parser.add_argument("--disable-tool-discord", action="store_true", help="Disable Discord messaging tools")
    parser.add_argument("--codex-jobs-dir", help="Directory for Codex job metadata and logs")
    parser.add_argument("--codex-workspace-dir", help="Default workspace for Codex tasks")
    parser.add_argument("--codex-docker-image", help="Docker image used for Codex agent tasks")
    parser.add_argument("--codex-host-codex-home", help="Host Codex home mounted into the Codex Docker container")
    parser.add_argument("--codex-host-gh-config-dir", help="Host GitHub CLI config directory mounted into the Codex Docker container")
    parser.add_argument("--codex-host-command", help="Codex executable used for explicitly confirmed host-mode tasks")
    parser.add_argument("--codex-dispatch-mode", choices=["exec", "app_server"], help="Codex dispatch backend: direct codex exec or Codex app-server")
    parser.add_argument("--codex-app-server-command", help="Codex executable used for app-server dispatch")
    parser.add_argument("--codex-app-server-url", help="Existing Codex app-server WebSocket URL used for app-server dispatch")
    parser.add_argument("--discord-enabled", action="store_true", help="Enable the Discord bot bridge")
    parser.add_argument("--disable-discord", action="store_true", help="Disable the Discord bot bridge")
    parser.add_argument("--discord-bot-token", help="Discord bot token")
    parser.add_argument("--discord-client-id", help="Discord bot application/client id")
    parser.add_argument("--discord-allowed-user-ids", help="Comma-separated allowlist of Discord user ids")
    parser.add_argument("--frontend", choices=["local", "vape-server"], help="Audio frontend to run")
    parser.add_argument("--vape-server-host", help="Host/IP for the VAPE satellite WebSocket server")
    parser.add_argument("--vape-server-port", type=int, help="Port for the VAPE satellite WebSocket server")
    parser.add_argument("--vape-server-path", help="WebSocket path for VAPE satellite clients")
    parser.add_argument("--vape-output-sample-rate", type=int, choices=[24000, 48000], help="PCM sample rate sent back to VAPE")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    return parser


def load_config(argv: Optional[Sequence[str]] = None) -> tuple[AppConfig, argparse.Namespace]:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    yaml_path = Path(args.config or os.getenv("LVA_CONFIG", "")).expanduser() if (args.config or os.getenv("LVA_CONFIG")) else None
    yaml_config: dict[str, Any] = {}
    if yaml_path is not None:
        yaml_config = _load_yaml(yaml_path)

    wake_word_dirs = _coerce_path_list(
        _pick(
            args.wake_word_dir,
            _env_list("LVA_WAKE_WORD_DIR"),
            _get_path(yaml_config, "audio.wake_word_dirs"),
            _get_path(yaml_config, "wakeword.directories"),
            [_WAKEWORDS_DIR],
        )
    )

    openai_api_key = _pick(
        os.getenv("OPENAI_API_KEY"),
        os.getenv("LVA_OPENAI_API_KEY"),
        _get_str(yaml_config, "openai.api_key"),
    )
    ha_url = _pick(
        args.ha_url,
        os.getenv("HOME_ASSISTANT_URL"),
        os.getenv("LVA_HA_URL"),
        _get_str(yaml_config, "home_assistant.url"),
    )
    ha_token = _pick(
        args.ha_token,
        os.getenv("HOME_ASSISTANT_TOKEN"),
        os.getenv("LVA_HA_TOKEN"),
        _get_str(yaml_config, "home_assistant.token"),
    )

    if not openai_api_key:
        parser.error("OpenAI API key is required via OPENAI_API_KEY, LVA_OPENAI_API_KEY, or config file")
    if not ha_url:
        parser.error("Home Assistant URL is required via --ha-url, HOME_ASSISTANT_URL, LVA_HA_URL, or config file")
    if not ha_token:
        parser.error("Home Assistant token is required via --ha-token, HOME_ASSISTANT_TOKEN, LVA_HA_TOKEN, or config file")

    verify_ssl = True
    if args.no_ha_verify_ssl:
        verify_ssl = False
    elif args.ha_verify_ssl:
        verify_ssl = True
    else:
        verify_ssl = bool(
            _pick(
                _env_bool("LVA_HA_VERIFY_SSL"),
                _env_bool("HOME_ASSISTANT_VERIFY_SSL"),
                _get_bool(yaml_config, "home_assistant.verify_ssl"),
                True,
            )
        )

    follow_up_after_tool_call = None
    if args.follow_up_after_tool_call:
        follow_up_after_tool_call = True
    elif args.no_follow_up_after_tool_call:
        follow_up_after_tool_call = False

    enable_tool_codex_agent = None
    if args.enable_tool_codex_agent:
        enable_tool_codex_agent = True
    elif args.disable_tool_codex_agent:
        enable_tool_codex_agent = False

    enable_tool_timer = None
    if args.enable_tool_timer:
        enable_tool_timer = True
    elif args.disable_tool_timer:
        enable_tool_timer = False

    enable_tool_discord = None
    if args.enable_tool_discord:
        enable_tool_discord = True
    elif args.disable_tool_discord:
        enable_tool_discord = False

    discord_enabled = None
    if args.discord_enabled:
        discord_enabled = True
    elif args.disable_discord:
        discord_enabled = False

    config = AppConfig(
        name=_pick(args.name, os.getenv("LVA_NAME"), _get_str(yaml_config, "device.name"), None),
        config_path=yaml_path,
        audio_input_device=_pick(
            args.audio_input_device,
            os.getenv("LVA_AUDIO_INPUT_DEVICE"),
            _get_str(yaml_config, "audio.input_device"),
            None,
        ),
        audio_input_block_size=int(
            _pick(
                args.audio_input_block_size,
                _env_int("LVA_AUDIO_INPUT_BLOCK_SIZE"),
                _get_int(yaml_config, "audio.input_block_size"),
                1024,
            )
        ),
        audio_output_device=_pick(
            args.audio_output_device,
            os.getenv("LVA_AUDIO_OUTPUT_DEVICE"),
            _get_str(yaml_config, "audio.output_device"),
            None,
        ),
        wake_word_dirs=wake_word_dirs,
        wake_model=_pick(args.wake_model, os.getenv("LVA_WAKE_MODEL"), _get_str(yaml_config, "wakeword.model"), "okay_nabu"),
        stop_model=_pick(args.stop_model, os.getenv("LVA_STOP_MODEL"), _get_str(yaml_config, "wakeword.stop_model"), "stop"),
        download_dir=_coerce_path(_pick(args.download_dir, os.getenv("LVA_DOWNLOAD_DIR"), _get_path(yaml_config, "wakeword.download_dir"), _REPO_DIR / "local")),
        refractory_seconds=float(
            _pick(
                args.refractory_seconds,
                _env_float("LVA_REFACTORY_SECONDS"),
                _env_float("LVA_REFRACTORY_SECONDS"),
                _get_float(yaml_config, "wakeword.refractory_seconds"),
                2.0,
            )
        ),
        wakeup_sound=_pick(
            args.wakeup_sound,
            os.getenv("LVA_WAKEUP_SOUND"),
            _get_str(yaml_config, "audio.wakeup_sound"),
            str(_SOUNDS_DIR / "wake_word_triggered.flac"),
        ),
        processing_sound=_pick(
            args.processing_sound,
            os.getenv("LVA_PROCESSING_SOUND"),
            _get_str(yaml_config, "audio.processing_sound"),
            str(_SOUNDS_DIR / "processing.wav"),
        ),
        tool_call_sound=_pick(
            args.tool_call_sound,
            os.getenv("LVA_TOOL_CALL_SOUND"),
            _get_str(yaml_config, "audio.tool_call_sound"),
            str(_SOUNDS_DIR / "tool_call_processing.wav"),
        ),
        session_end_sound=_pick(
            args.session_end_sound,
            os.getenv("LVA_SESSION_END_SOUND"),
            _get_str(yaml_config, "audio.session_end_sound"),
            str(_SOUNDS_DIR / "mute_switch_on.flac"),
        ),
        timer_finished_sound=_pick(
            args.timer_finished_sound,
            os.getenv("LVA_TIMER_FINISHED_SOUND"),
            _get_str(yaml_config, "audio.timer_finished_sound"),
            str(_SOUNDS_DIR / "timer_finished.flac"),
        ),
        preferences_file=_coerce_path(_pick(args.preferences_file, os.getenv("LVA_PREFERENCES_FILE"), _get_path(yaml_config, "device.preferences_file"), _REPO_DIR / "preferences.json")),
        debug=bool(_pick(args.debug, _env_bool("LVA_DEBUG"), _get_bool(yaml_config, "device.debug"), False)),
        openai_api_key=openai_api_key,
        openai_model=_pick(args.openai_model, os.getenv("LVA_OPENAI_MODEL"), _get_str(yaml_config, "openai.model"), "gpt-realtime"),
        openai_voice=_pick(args.openai_voice, os.getenv("LVA_OPENAI_VOICE"), _get_str(yaml_config, "openai.voice"), "marin"),
        openai_api_base=_pick(args.openai_api_base, os.getenv("OPENAI_BASE_URL"), os.getenv("LVA_OPENAI_API_BASE"), _get_str(yaml_config, "openai.api_base"), None),
        openai_instructions=_pick(args.openai_instructions, os.getenv("LVA_OPENAI_INSTRUCTIONS"), _get_str(yaml_config, "openai.instructions"), DEFAULT_INSTRUCTIONS),
        ha_url=str(ha_url).rstrip("/"),
        ha_token=ha_token,
        ha_verify_ssl=verify_ssl,
        session_timeout_seconds=float(_pick(args.session_timeout_seconds, _env_float("LVA_SESSION_TIMEOUT_SECONDS"), _get_float(yaml_config, "runtime.session_timeout_seconds"), 20.0)),
        vad_threshold=float(_pick(args.vad_threshold, _env_float("LVA_VAD_THRESHOLD"), _get_float(yaml_config, "runtime.vad_threshold"), 0.005)),
        min_speech_seconds=float(_pick(args.min_speech_seconds, _env_float("LVA_MIN_SPEECH_SECONDS"), _get_float(yaml_config, "runtime.min_speech_seconds"), 0.2)),
        end_silence_seconds=float(_pick(args.end_silence_seconds, _env_float("LVA_END_SILENCE_SECONDS"), _get_float(yaml_config, "runtime.end_silence_seconds"), 0.8)),
        follow_up_after_tool_call=bool(_pick(follow_up_after_tool_call, _env_bool("LVA_FOLLOW_UP_AFTER_TOOL_CALL"), _get_bool(yaml_config, "runtime.follow_up_after_tool_call"), False)),
        memory_interactions_count=max(
            0,
            int(
                _pick(
                    args.memory_interactions_count,
                    _env_int("LVA_MEMORY_INTERACTIONS_COUNT"),
                    _get_int(yaml_config, "runtime.memory_interactions_count"),
                    6,
                )
            ),
        ),
        enable_tool_get_entities=bool(_pick(_env_bool("LVA_ENABLE_TOOL_GET_ENTITIES"), _get_bool(yaml_config, "tools.enable_get_entities"), True)),
        enable_tool_get_state=bool(_pick(_env_bool("LVA_ENABLE_TOOL_GET_STATE"), _get_bool(yaml_config, "tools.enable_get_state"), True)),
        enable_tool_call_service=bool(_pick(_env_bool("LVA_ENABLE_TOOL_CALL_SERVICE"), _get_bool(yaml_config, "tools.enable_call_service"), True)),
        enable_tool_web_search=bool(_pick(_env_bool("LVA_ENABLE_TOOL_WEB_SEARCH"), _get_bool(yaml_config, "tools.enable_web_search"), True)),
        enable_tool_codex_agent=bool(_pick(enable_tool_codex_agent, _env_bool("LVA_ENABLE_TOOL_CODEX_AGENT"), _get_bool(yaml_config, "tools.enable_codex_agent"), True)),
        enable_tool_timer=bool(_pick(enable_tool_timer, _env_bool("LVA_ENABLE_TOOL_TIMER"), _get_bool(yaml_config, "tools.enable_timer"), True)),
        enable_tool_discord=bool(_pick(enable_tool_discord, _env_bool("LVA_ENABLE_TOOL_DISCORD"), _get_bool(yaml_config, "tools.enable_discord"), True)),
        codex_jobs_dir=_coerce_path(_pick(args.codex_jobs_dir, os.getenv("LVA_CODEX_JOBS_DIR"), _get_path(yaml_config, "codex.jobs_dir"), _REPO_DIR / "local" / "codex_jobs")),
        codex_workspace_dir=_coerce_path(_pick(args.codex_workspace_dir, os.getenv("LVA_CODEX_WORKSPACE_DIR"), _get_path(yaml_config, "codex.workspace_dir"), _REPO_DIR)),
        codex_docker_image=str(_pick(args.codex_docker_image, os.getenv("LVA_CODEX_DOCKER_IMAGE"), _get_str(yaml_config, "codex.docker_image"), "lva-codex-agent:latest")),
        codex_host_codex_home=_coerce_path(_pick(args.codex_host_codex_home, os.getenv("LVA_CODEX_HOST_CODEX_HOME"), _get_path(yaml_config, "codex.host_codex_home"), Path.home() / ".codex")),
        codex_host_gh_config_dir=_coerce_path(_pick(args.codex_host_gh_config_dir, os.getenv("LVA_CODEX_HOST_GH_CONFIG_DIR"), _get_path(yaml_config, "codex.host_gh_config_dir"), Path.home() / ".config" / "gh")),
        codex_host_command=str(_pick(args.codex_host_command, os.getenv("LVA_CODEX_HOST_COMMAND"), _get_str(yaml_config, "codex.host_command"), "codex")),
        codex_dispatch_mode=str(_pick(args.codex_dispatch_mode, os.getenv("LVA_CODEX_DISPATCH_MODE"), _get_str(yaml_config, "codex.dispatch_mode"), "exec")).strip().lower(),
        codex_app_server_command=str(
            _pick(
                args.codex_app_server_command,
                os.getenv("LVA_CODEX_APP_SERVER_COMMAND"),
                _get_str(yaml_config, "codex.app_server_command"),
                args.codex_host_command,
                os.getenv("LVA_CODEX_HOST_COMMAND"),
                _get_str(yaml_config, "codex.host_command"),
                "codex",
            )
        ),
        codex_app_server_url=str(_pick(args.codex_app_server_url, os.getenv("LVA_CODEX_APP_SERVER_URL"), _get_str(yaml_config, "codex.app_server_url")) or ""),
        discord_enabled=bool(_pick(discord_enabled, _env_bool("LVA_DISCORD_ENABLED"), _get_bool(yaml_config, "discord.enabled"), True)),
        discord_bot_token=str(_pick(args.discord_bot_token, _get_str(yaml_config, "discord.bot_token"), os.getenv("LVA_DISCORD_BOT_TOKEN"), os.getenv("DISCORD_BOT_TOKEN"), "")),
        discord_client_id=str(_pick(args.discord_client_id, os.getenv("LVA_DISCORD_CLIENT_ID"), _get_str(yaml_config, "discord.client_id"), "")),
        discord_allowed_user_ids=_coerce_string_list(
            _pick(
                args.discord_allowed_user_ids,
                os.getenv("LVA_DISCORD_ALLOWED_USER_IDS"),
                _get(yaml_config, "discord.allowed_user_ids"),
                "130283160301862913,468850569986179084",
            )
        ),
        frontend=str(_pick(args.frontend, os.getenv("LVA_FRONTEND"), _get_str(yaml_config, "frontend"), "local")),
        vape_server_host=str(_pick(args.vape_server_host, os.getenv("LVA_VAPE_SERVER_HOST"), _get_str(yaml_config, "vape_server.host"), "0.0.0.0")),
        vape_server_port=int(_pick(args.vape_server_port, _env_int("LVA_VAPE_SERVER_PORT"), _get_int(yaml_config, "vape_server.port"), 8765)),
        vape_server_path=str(_pick(args.vape_server_path, os.getenv("LVA_VAPE_SERVER_PATH"), _get_str(yaml_config, "vape_server.path"), "/vape")),
        vape_output_sample_rate=int(_pick(args.vape_output_sample_rate, _env_int("LVA_VAPE_OUTPUT_SAMPLE_RATE"), _get_int(yaml_config, "vape_server.output_sample_rate"), 24000)),
    )
    return config, args


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ValueError(f"Config file must contain a mapping: {path}")
    return loaded


def _pick(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and value == "":
            continue
        return value
    return None


def _get(config: dict[str, Any], dotted_path: str) -> Any:
    current: Any = config
    for part in dotted_path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _get_str(config: dict[str, Any], dotted_path: str) -> Optional[str]:
    value = _get(config, dotted_path)
    return value if isinstance(value, str) else None


def _get_int(config: dict[str, Any], dotted_path: str) -> Optional[int]:
    value = _get(config, dotted_path)
    return int(value) if isinstance(value, (int, float)) else None


def _get_float(config: dict[str, Any], dotted_path: str) -> Optional[float]:
    value = _get(config, dotted_path)
    return float(value) if isinstance(value, (int, float)) else None


def _get_bool(config: dict[str, Any], dotted_path: str) -> Optional[bool]:
    value = _get(config, dotted_path)
    return value if isinstance(value, bool) else None


def _get_path(config: dict[str, Any], dotted_path: str) -> Optional[Any]:
    value = _get(config, dotted_path)
    if isinstance(value, (str, Path, list)):
        return value
    return None


def _coerce_path(value: str | Path) -> Path:
    return Path(value).expanduser()


def _coerce_path_list(value: Any) -> list[Path]:
    if isinstance(value, (str, Path)):
        return [Path(value).expanduser()]
    if isinstance(value, list):
        return [Path(item).expanduser() for item in value]
    raise ValueError(f"Unsupported path list value: {value!r}")


def _coerce_string_list(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return ",".join(str(item).strip() for item in value if str(item).strip())
    return str(value).strip()


def _env_bool(name: str) -> Optional[bool]:
    value = os.getenv(name)
    if value is None:
        return None
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str) -> Optional[int]:
    value = os.getenv(name)
    return int(value) if value is not None else None


def _env_float(name: str) -> Optional[float]:
    value = os.getenv(name)
    return float(value) if value is not None else None


def _env_list(name: str) -> Optional[list[str]]:
    value = os.getenv(name)
    if value is None:
        return None
    return [part for part in value.split(":") if part]
