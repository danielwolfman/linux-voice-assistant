import asyncio
from types import SimpleNamespace

from linux_voice_assistant.tools.discord_bridge import DiscordBotService, DiscordTool, discord_origin_session_id, discord_user_id_from_origin, parse_discord_user_ids


def test_parse_discord_user_ids_accepts_lists_and_text():
    assert parse_discord_user_ids(["130283160301862913", "bad", "468850569986179084"]) == [
        "130283160301862913",
        "468850569986179084",
    ]
    assert parse_discord_user_ids("130283160301862913, 130283160301862913 468850569986179084") == [
        "130283160301862913",
        "468850569986179084",
    ]


def test_discord_origin_session_id_round_trips():
    origin = discord_origin_session_id("130283160301862913")
    assert origin == "discord:130283160301862913"
    assert discord_user_id_from_origin(origin) == "130283160301862913"
    assert discord_user_id_from_origin("vape-session") == ""


def test_discord_tool_requires_message_and_uses_allowlist():
    asyncio.run(_test_discord_tool_requires_message_and_uses_allowlist())


async def _test_discord_tool_requires_message_and_uses_allowlist():
    service = SimpleNamespace(send_message=_fake_send_message)
    tool = DiscordTool(service)  # type: ignore[arg-type]

    assert await tool.execute_tool("send_discord_message", {"message": ""}) == {
        "status": "error",
        "error": "A non-empty message is required.",
    }
    result = await tool.execute_tool("send_discord_message", {"message": "hello"})
    assert result == {"status": "sent", "message": "hello", "user_ids": None}


async def _fake_send_message(message, user_ids=None):
    return {"status": "sent", "message": message, "user_ids": user_ids}


def test_discord_service_formats_dm_tool_error_when_not_connected(tmp_path):
    service = DiscordBotService(
        token="",
        client_id="1504771552921518190",
        allowed_user_ids="130283160301862913,468850569986179084",
        codex_manager=SimpleNamespace(),  # type: ignore[arg-type]
    )

    result = asyncio.run(service.send_message("hello", ["130283160301862913"]))

    assert result["status"] == "error"
    assert result["error"] == "Discord bot is not connected."
