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
    assert config.follow_up_after_tool_call is False
    assert config.enable_tool_get_entities is True
    assert config.enable_tool_get_state is True
    assert config.enable_tool_call_service is True
    assert config.enable_tool_web_search is True


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
