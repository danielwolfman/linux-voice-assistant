import asyncio
from pathlib import Path
from types import SimpleNamespace

from linux_voice_assistant.tools import discord_bridge
from linux_voice_assistant.tools.codex_agent import CodexJob
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


def test_discord_accepts_job_with_reaction_and_no_reply():
    asyncio.run(_test_discord_accepts_job_with_reaction_and_no_reply())


async def _test_discord_accepts_job_with_reaction_and_no_reply():
    manager = FakeCodexManager({"status": "accepted", "job": {"id": "job-1", "status": "running"}})
    service = DiscordBotService(
        token="",
        client_id="1504771552921518190",
        allowed_user_ids="130283160301862913",
        codex_manager=manager,  # type: ignore[arg-type]
    )
    message = FakeMessage("130283160301862913", "fix the tests")

    await service._handle_message(message)  # pylint: disable=protected-access
    await service.close()

    assert message.reactions == ["\N{EYES}"]
    assert message.channel.messages == []
    assert manager.started_with == ("130283160301862913", "fix the tests")
    assert manager.allow_parallel is True


def test_discord_ignores_unallowed_user_without_reaction_or_reply():
    asyncio.run(_test_discord_ignores_unallowed_user_without_reaction_or_reply())


async def _test_discord_ignores_unallowed_user_without_reaction_or_reply():
    manager = FakeCodexManager({"status": "accepted", "job": {"id": "job-1", "status": "running"}})
    service = DiscordBotService(
        token="",
        client_id="1504771552921518190",
        allowed_user_ids="130283160301862913",
        codex_manager=manager,  # type: ignore[arg-type]
    )
    message = FakeMessage("468850569986179084", "fix the tests")

    await service._handle_message(message)  # pylint: disable=protected-access

    assert message.reactions == []
    assert message.channel.messages == []
    assert manager.started_with is None


def test_discord_completion_replies_only_final_output():
    asyncio.run(_test_discord_completion_replies_only_final_output())


async def _test_discord_completion_replies_only_final_output():
    manager = FakeCodexManager({"status": "accepted", "job": {"id": "job-1", "status": "running"}})
    service = DiscordBotService(
        token="",
        client_id="1504771552921518190",
        allowed_user_ids="130283160301862913",
        codex_manager=manager,  # type: ignore[arg-type]
    )
    message = FakeMessage("130283160301862913", "fix the tests")
    await service._handle_message(message)  # pylint: disable=protected-access

    job = CodexJob(
        id="job-1",
        task="fix the tests",
        workspace=Path("/tmp"),
        execution_mode="docker",
        origin_session_id=discord_origin_session_id("130283160301862913"),
        status="succeeded",
        final_output="Changed the test and it passes.",
    )
    await service.notify_codex_job_finished(job)

    assert message.channel.messages == [("Changed the test and it passes.", message)]


def test_discord_sends_still_working_after_delay(monkeypatch):
    asyncio.run(_test_discord_sends_still_working_after_delay(monkeypatch))


async def _test_discord_sends_still_working_after_delay(monkeypatch):
    monkeypatch.setattr(discord_bridge, "_STILL_WORKING_SECONDS", 0)
    manager = FakeCodexManager({"status": "accepted", "job": {"id": "job-1", "status": "running"}})
    service = DiscordBotService(
        token="",
        client_id="1504771552921518190",
        allowed_user_ids="130283160301862913",
        codex_manager=manager,  # type: ignore[arg-type]
    )
    message = FakeMessage("130283160301862913", "fix the tests")

    await service._handle_message(message)  # pylint: disable=protected-access
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert message.channel.messages == [("I'm still working on it", message)]
    await service.close()


def test_discord_reply_chain_is_passed_as_codex_context():
    asyncio.run(_test_discord_reply_chain_is_passed_as_codex_context())


async def _test_discord_reply_chain_is_passed_as_codex_context():
    manager = FakeCodexManager({"status": "accepted", "job": {"id": "job-1", "status": "running"}})
    service = DiscordBotService(
        token="",
        client_id="1504771552921518190",
        allowed_user_ids="130283160301862913",
        codex_manager=manager,  # type: ignore[arg-type]
    )
    channel = FakeChannel()
    first_user = FakeMessage("130283160301862913", "please inspect this repo", channel=channel, message_id=1)
    first_bot = FakeMessage("1504771552921518190", "I found the backend entrypoint.", channel=channel, message_id=2, bot=True, reference=first_user)
    second_user = FakeMessage("130283160301862913", "check the discord bridge too", channel=channel, message_id=3, reference=first_bot)
    second_bot = FakeMessage("1504771552921518190", "The bridge starts Codex jobs.", channel=channel, message_id=4, bot=True, reference=second_user)
    current = FakeMessage(
        "130283160301862913",
        "now implement the missing tests",
        channel=channel,
        message_id=5,
        reference=second_bot,
    )

    await service._handle_message(current)  # pylint: disable=protected-access
    await service.close()

    assert current.reactions == ["\N{EYES}"]
    task = manager.started_with[1]
    assert "Previous Discord replies, oldest to newest:" in task
    assert "User 130283160301862913: please inspect this repo" in task
    assert "Mycroft: I found the backend entrypoint." in task
    assert "User 130283160301862913: check the discord bridge too" in task
    assert "Mycroft: The bridge starts Codex jobs." in task
    assert "Current Discord task:\nnow implement the missing tests" in task


def test_discord_server_reply_to_bot_starts_codex_without_mention():
    asyncio.run(_test_discord_server_reply_to_bot_starts_codex_without_mention())


async def _test_discord_server_reply_to_bot_starts_codex_without_mention():
    manager = FakeCodexManager({"status": "accepted", "job": {"id": "job-1", "status": "running"}})
    service = DiscordBotService(
        token="",
        client_id="1504771552921518190",
        allowed_user_ids="130283160301862913",
        codex_manager=manager,  # type: ignore[arg-type]
    )
    channel = FakeChannel()
    bot_reply = FakeMessage("1504771552921518190", "Done.", channel=channel, message_id=1, bot=True, guild=True)
    user_reply = FakeMessage("130283160301862913", "continue with docs", channel=channel, message_id=2, reference=bot_reply, guild=True)

    await service._handle_message(user_reply)  # pylint: disable=protected-access
    await service.close()

    assert user_reply.reactions == ["\N{EYES}"]
    assert manager.started_with[1].endswith("Current Discord task:\ncontinue with docs")


class FakeCodexManager:
    def __init__(self, start_result):
        self.start_result = start_result
        self.started_with = None
        self.allow_parallel = None

    async def start_task(self, arguments, *, origin_session_id, allow_parallel=False):
        self.started_with = (discord_user_id_from_origin(origin_session_id), arguments["task"])
        self.allow_parallel = allow_parallel
        return self.start_result

    def get_status(self, job_id=""):
        return {"status": "ok", "job": {"id": job_id or "job-1", "status": "running"}}

    async def cancel_task(self, job_id=""):
        return {"status": "not_running", "job": {"id": job_id or "job-1", "status": "cancelled"}}


class FakeChannel:
    def __init__(self):
        self.messages = []

    async def send(self, text, reference=None):
        self.messages.append((text, reference))


class FakeMessage:
    def __init__(self, user_id, content, *, channel=None, message_id=1, bot=False, reference=None, guild=False):
        self.id = message_id
        self.author = SimpleNamespace(
            id=user_id,
            bot=bot,
            display_name="Mycroft" if bot else f"User {user_id}",
        )
        self.content = content
        self.guild = object() if guild else None
        self.channel = channel or FakeChannel()
        self.reference = SimpleNamespace(resolved=reference, message_id=getattr(reference, "id", None)) if reference is not None else None
        self.mentions = []
        self.reactions = []

    async def add_reaction(self, reaction):
        self.reactions.append(reaction)
