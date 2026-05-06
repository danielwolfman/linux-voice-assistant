import asyncio
from types import SimpleNamespace

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
