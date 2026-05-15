import asyncio
from types import SimpleNamespace

from linux_voice_assistant.memory import Interaction
from linux_voice_assistant.realtime.client import OpenAIRealtimeClient


def test_voice_change_closes_active_realtime_session():
    async def run():
        client = object.__new__(OpenAIRealtimeClient)
        client._model = "gpt-realtime"
        client._voice = "coral"
        client._instructions = "test"
        client._connection = object()
        client._closed = False

        async def close():
            client._closed = True
            client._connection = None

        client.close = close

        await client.update_session_settings(voice="sage")

        assert client._voice == "sage"
        assert client._closed
        assert client._connection is None

    asyncio.run(run())


def test_instruction_change_updates_active_realtime_session():
    async def run():
        updated = []

        class FakeSession:
            async def update(self, *, session):
                updated.append(session)

        client = object.__new__(OpenAIRealtimeClient)
        client._model = "gpt-realtime"
        client._voice = "coral"
        client._instructions = "old"
        client._connection = SimpleNamespace(session=FakeSession())
        client._build_session_config = lambda: {"instructions": client._instructions}

        await client.update_session_settings(instructions="new")

        assert client._instructions == "new"
        assert updated == [{"instructions": "new"}]

    asyncio.run(run())


def test_memory_context_is_appended_to_session_instructions():
    async def run():
        updated = []

        class FakeSession:
            async def update(self, *, session):
                updated.append(session)

        client = object.__new__(OpenAIRealtimeClient)
        client._model = "gpt-realtime"
        client._voice = "coral"
        client._instructions = "base instructions"
        client._memory_context = ""
        client._connection = SimpleNamespace(session=FakeSession())
        client._tools = SimpleNamespace(tool_definitions=lambda: [])

        await client.update_memory_context(
            [
                Interaction(user="turn one", assistant="answer one", timestamp="2026-05-15T00:00:00+00:00"),
                Interaction(user="turn two", assistant="answer two", timestamp="2026-05-15T00:01:00+00:00"),
            ]
        )

        instructions = updated[0]["instructions"]
        assert instructions.startswith("base instructions")
        assert "Recent interaction memory:" in instructions
        assert "User: turn one" in instructions
        assert "Assistant: answer two" in instructions

    asyncio.run(run())


def test_response_create_event_includes_current_voice():
    client = object.__new__(OpenAIRealtimeClient)
    client._voice = "cedar"

    event = client._build_response_create_event()

    assert event == {
        "type": "response.create",
        "response": {
            "output_modalities": ["audio"],
            "audio": {
                "output": {
                    "voice": "cedar",
                    "format": {"type": "audio/pcm", "rate": 24000},
                },
            },
        },
    }


def test_create_text_response_sends_user_message_and_response():
    async def run():
        sent = []

        class FakeConnection:
            async def send(self, payload):
                sent.append(payload)

        client = object.__new__(OpenAIRealtimeClient)
        client._connection = FakeConnection()
        client._build_response_create_event = lambda: {"type": "response.create"}

        async def connect():
            return None

        client.connect = connect

        await client.create_text_response("Codex finished")

        assert sent[0]["type"] == "conversation.item.create"
        assert sent[0]["item"]["content"][0]["type"] == "input_text"
        assert sent[0]["item"]["content"][0]["text"] == "Codex finished"
        assert sent[1] == {"type": "response.create"}

    asyncio.run(run())
