import asyncio

from linux_voice_assistant.tools.timer import TimerManager, TimerTool, _format_duration


class FakeHomeAssistantTools:
    def __init__(self, entities=None):
        self.entities = entities or []
        self.calls = []

    async def get_entities(self, query=None, area=None, domain=None, limit=10):
        del query, area, domain, limit
        return {"count": len(self.entities), "entities": self.entities}

    async def call_service(self, domain, service, target, data):
        self.calls.append((domain, service, target, data))
        return []


def test_timer_manager_completes_and_invokes_callback():
    asyncio.run(_test_timer_manager_completes_and_invokes_callback())


async def _test_timer_manager_completes_and_invokes_callback():
    completed = []

    async def on_complete(timer):
        completed.append(timer)

    manager = TimerManager(completion_callback=on_complete, finished_sound="/tmp/timer.flac")
    timer = await manager.start_timer(duration_seconds=1, label="tea", origin_session_id="session-1")

    await asyncio.sleep(1.05)

    assert timer.status == "finished"
    assert timer.finished_at is not None
    assert timer.finished_sound == "/tmp/timer.flac"
    assert completed == [timer]


def test_timer_tool_starts_local_timer_and_mirrors_unambiguous_ha_timer():
    asyncio.run(_test_timer_tool_starts_local_timer_and_mirrors_unambiguous_ha_timer())


async def _test_timer_tool_starts_local_timer_and_mirrors_unambiguous_ha_timer():
    ha_tools = FakeHomeAssistantTools(
        [
            {
                "entity_id": "timer.voice_assistant",
                "name": "Voice Assistant Timer",
                "state": "idle",
            }
        ]
    )
    manager = TimerManager()
    tool = TimerTool(manager, "session-1", ha_tools)  # type: ignore[arg-type]

    result = await tool.execute_tool("start_timer", {"duration_seconds": 90, "label": "pasta"})

    assert result["status"] == "accepted"
    assert result["timer"]["label"] == "pasta"
    assert result["timer"]["home_assistant_entity_id"] == "timer.voice_assistant"
    assert ha_tools.calls == [
        ("timer", "start", {"entity_id": "timer.voice_assistant"}, {"duration": "00:01:30"}),
    ]
    await manager.close()


def test_timer_tool_uses_backend_only_when_ha_timer_is_ambiguous():
    asyncio.run(_test_timer_tool_uses_backend_only_when_ha_timer_is_ambiguous())


async def _test_timer_tool_uses_backend_only_when_ha_timer_is_ambiguous():
    ha_tools = FakeHomeAssistantTools(
        [
            {"entity_id": "timer.kitchen", "name": "Kitchen", "state": "idle"},
            {"entity_id": "timer.laundry", "name": "Laundry", "state": "idle"},
        ]
    )
    manager = TimerManager()
    tool = TimerTool(manager, "session-1", ha_tools)  # type: ignore[arg-type]

    result = await tool.execute_tool("start_timer", {"duration_seconds": 30})

    assert result["status"] == "accepted"
    assert result["timer"]["home_assistant_entity_id"] is None
    assert result["timer"]["home_assistant"]["status"] == "not_used"
    assert ha_tools.calls == []
    await manager.close()


def test_timer_tool_cancels_next_timer():
    asyncio.run(_test_timer_tool_cancels_next_timer())


async def _test_timer_tool_cancels_next_timer():
    manager = TimerManager()
    tool = TimerTool(manager, "session-1", FakeHomeAssistantTools())  # type: ignore[arg-type]
    await tool.execute_tool("start_timer", {"duration_seconds": 60, "label": "later"})

    result = await tool.execute_tool("cancel_timer", {})

    assert result["status"] == "cancelled"
    assert result["timer"]["status"] == "cancelled"
    assert manager.get_timers() == []


def test_format_duration_allows_long_hour_values():
    assert _format_duration(90061) == "25:01:01"
