import asyncio

from aiohttp.test_utils import make_mocked_request

from linux_voice_assistant.tools.codex_tasks import CodexTaskService, _parse_create_task_payload, is_silent_codex_task_origin
from linux_voice_assistant.tools.discord_bridge import discord_channel_id_from_origin, discord_user_id_from_origin


def test_codex_task_payload_builds_discord_channel_origin_and_context():
    arguments, origin, allow_parallel, delay, error = _parse_create_task_payload(
        {
            "task": "Write a message to Tally.",
            "context": {"distance_km": 3.2},
            "origin_language": "he",
            "delivery": {"type": "discord_channel", "channel_id": "1504773998330773646"},
            "delay_seconds": 90,
        }
    )

    assert error == ""
    assert arguments["execution_mode"] == "docker"
    assert arguments["origin_language"] == "he"
    assert "Context:" in arguments["task"]
    assert '"distance_km": 3.2' in arguments["task"]
    assert discord_channel_id_from_origin(origin) == "1504773998330773646"
    assert allow_parallel is True
    assert delay == 90


def test_codex_task_payload_supports_discord_user_and_silent_delivery():
    _, user_origin, _, _, error = _parse_create_task_payload(
        {"task": "summarize", "delivery": {"type": "discord_user", "user_id": "130283160301862913"}}
    )
    assert error == ""
    assert discord_user_id_from_origin(user_origin) == "130283160301862913"

    _, silent_origin, _, _, error = _parse_create_task_payload({"task": "summarize", "delivery": {"type": "silent"}})
    assert error == ""
    assert is_silent_codex_task_origin(silent_origin)


def test_codex_task_service_dispatches_immediate_task():
    asyncio.run(_test_codex_task_service_dispatches_immediate_task())


async def _test_codex_task_service_dispatches_immediate_task():
    manager = FakeCodexManager()
    service = CodexTaskService(codex_manager=manager)  # type: ignore[arg-type]
    request = make_mocked_request("POST", "/codex/tasks")
    request.json = _json_method({"task": "do it", "delivery": {"type": "discord_channel", "channel_id": "1504773998330773646"}})  # type: ignore[method-assign]

    response = await service.handle_create_task(request)

    assert response.status == 202
    assert manager.started_with["arguments"]["task"] == "do it"
    assert discord_channel_id_from_origin(manager.started_with["origin_session_id"]) == "1504773998330773646"


class FakeCodexManager:
    def __init__(self):
        self.started_with = None

    async def start_task(self, arguments, *, origin_session_id, allow_parallel=False):
        self.started_with = {
            "arguments": arguments,
            "origin_session_id": origin_session_id,
            "allow_parallel": allow_parallel,
        }
        return {"status": "accepted", "job": {"id": "job-1"}}


def _json_method(payload):
    async def json_method():
        return payload

    return json_method
