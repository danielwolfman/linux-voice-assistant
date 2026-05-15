import os

from linux_voice_assistant.config import load_config


def test_load_config_prefers_cli_over_env_and_yaml(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
device:
  name: YAML Name
home_assistant:
  url: http://yaml.local:8123
  token: yaml-token
openai:
  api_key: yaml-openai
  model: gpt-realtime
runtime:
  session_timeout_seconds: 30
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("OPENAI_API_KEY", "env-openai")
    monkeypatch.setenv("HOME_ASSISTANT_URL", "http://env.local:8123")
    monkeypatch.setenv("HOME_ASSISTANT_TOKEN", "env-token")

    config, _ = load_config(["--config", os.fspath(config_path), "--name", "CLI Name"])

    assert config.name == "CLI Name"
    assert config.openai_api_key == "env-openai"
    assert config.ha_url == "http://env.local:8123"
    assert config.ha_token == "env-token"
    assert config.session_timeout_seconds == 30
    assert config.processing_sound.endswith("processing.wav")
    assert config.tool_call_sound.endswith("tool_call_processing.wav")
    assert config.session_end_sound.endswith("mute_switch_on.flac")
    assert config.timer_finished_sound.endswith("timer_finished.flac")
    assert config.follow_up_after_tool_call is False
    assert config.memory_interactions_count == 6
    assert config.enable_tool_get_entities is True
    assert config.enable_tool_get_state is True
    assert config.enable_tool_call_service is True
    assert config.enable_tool_web_search is True
    assert config.enable_tool_codex_agent is True
    assert config.enable_tool_timer is True
    assert config.enable_tool_discord is True
    assert config.codex_docker_image == "lva-codex-agent:latest"
    assert config.discord_enabled is True
    assert config.discord_allowed_user_ids == "130283160301862913,468850569986179084"


def test_load_config_reads_vape_server_options(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
home_assistant:
  url: http://yaml.local:8123
  token: yaml-token
openai:
  api_key: yaml-openai
vape_server:
  host: 0.0.0.0
  port: 8765
  path: /vape
  output_sample_rate: 48000
""",
        encoding="utf-8",
    )

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("HOME_ASSISTANT_URL", raising=False)
    monkeypatch.delenv("HOME_ASSISTANT_TOKEN", raising=False)

    config, _ = load_config(["--config", os.fspath(config_path), "--frontend", "vape-server"])

    assert config.frontend == "vape-server"
    assert config.vape_server_host == "0.0.0.0"
    assert config.vape_server_port == 8765
    assert config.vape_server_path == "/vape"
    assert config.vape_output_sample_rate == 48000


def test_load_config_reads_codex_options(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    workspace = tmp_path / "workspace"
    jobs = tmp_path / "jobs"
    codex_home = tmp_path / ".codex"
    gh_config = tmp_path / ".config" / "gh"
    config_path.write_text(
        f"""
home_assistant:
  url: http://yaml.local:8123
  token: yaml-token
openai:
  api_key: yaml-openai
tools:
  enable_codex_agent: false
codex:
  jobs_dir: {jobs}
  workspace_dir: {workspace}
  docker_image: custom-codex:latest
  host_codex_home: {codex_home}
  host_gh_config_dir: {gh_config}
  host_command: /home/daniel/.local/bin/codex
""",
        encoding="utf-8",
    )

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("HOME_ASSISTANT_URL", raising=False)
    monkeypatch.delenv("HOME_ASSISTANT_TOKEN", raising=False)

    config, _ = load_config(["--config", os.fspath(config_path)])

    assert config.enable_tool_codex_agent is False
    assert config.codex_jobs_dir == jobs
    assert config.codex_workspace_dir == workspace
    assert config.codex_docker_image == "custom-codex:latest"
    assert config.codex_host_codex_home == codex_home
    assert config.codex_host_gh_config_dir == gh_config
    assert config.codex_host_command == "/home/daniel/.local/bin/codex"


def test_load_config_reads_memory_interactions_count(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
home_assistant:
  url: http://yaml.local:8123
  token: yaml-token
openai:
  api_key: yaml-openai
runtime:
  memory_interactions_count: 12
""",
        encoding="utf-8",
    )

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("HOME_ASSISTANT_URL", raising=False)
    monkeypatch.delenv("HOME_ASSISTANT_TOKEN", raising=False)

    config, _ = load_config(["--config", os.fspath(config_path)])

    assert config.memory_interactions_count == 12


def test_load_config_reads_discord_options(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
home_assistant:
  url: http://yaml.local:8123
  token: yaml-token
openai:
  api_key: yaml-openai
tools:
  enable_discord: false
discord:
  enabled: true
  client_id: "1504771552921518190"
  allowed_user_ids:
    - "130283160301862913"
    - "468850569986179084"
""",
        encoding="utf-8",
    )

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("HOME_ASSISTANT_URL", raising=False)
    monkeypatch.delenv("HOME_ASSISTANT_TOKEN", raising=False)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "env-discord-token")

    config, _ = load_config(["--config", os.fspath(config_path)])

    assert config.enable_tool_discord is False
    assert config.discord_enabled is True
    assert config.discord_bot_token == "env-discord-token"
    assert config.discord_client_id == "1504771552921518190"
    assert config.discord_allowed_user_ids == "130283160301862913,468850569986179084"
