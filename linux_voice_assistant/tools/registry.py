"""Runtime tool registry for Home Assistant and web tools."""

from __future__ import annotations

from typing import Any

from ..ha_tools.client import HomeAssistantToolBridge
from .codex_agent import CodexAgentTool
from .timer import TimerTool
from .web_search import WebSearchTool


class ToolRegistry:
    def __init__(
        self,
        ha_tools: HomeAssistantToolBridge,
        web_search: WebSearchTool,
        codex_agent: CodexAgentTool | None = None,
        timer_tool: TimerTool | None = None,
    ) -> None:
        self._ha_tools = ha_tools
        self._web_search = web_search
        self._codex_agent = codex_agent
        self._timer_tool = timer_tool
        self._enabled_tools = {
            "get_entities": True,
            "get_state": True,
            "call_service": True,
            "web_search": True,
            "start_codex_task": True,
            "get_codex_status": True,
            "cancel_codex_task": True,
            "start_timer": True,
            "get_timers": True,
            "cancel_timer": True,
        }

    async def close(self) -> None:
        await self._ha_tools.close()
        await self._web_search.close()
        if self._codex_agent is not None:
            await self._codex_agent.close()
        if self._timer_tool is not None:
            await self._timer_tool.close()

    def set_enabled_tools(self, enabled_tools: dict[str, bool]) -> None:
        self._enabled_tools.update(enabled_tools)

    def tool_definitions(self) -> list[dict[str, Any]]:
        definitions = []
        for definition in self._ha_tools.tool_definitions():
            if self._enabled_tools.get(str(definition["name"]), True):
                definitions.append(definition)
        if self._enabled_tools.get("web_search", True):
            definitions.append(self._web_search.tool_definition())
        if self._codex_agent is not None:
            for definition in self._codex_agent.tool_definitions():
                if self._enabled_tools.get(str(definition["name"]), True):
                    definitions.append(definition)
        if self._timer_tool is not None:
            for definition in self._timer_tool.tool_definitions():
                if self._enabled_tools.get(str(definition["name"]), True):
                    definitions.append(definition)
        return definitions

    async def execute_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self._enabled_tools.get(name, True):
            return {"error": f"Tool disabled: {name}"}
        if name == "web_search":
            return await self._web_search.execute(arguments)
        if self._codex_agent is not None and name in {"start_codex_task", "get_codex_status", "cancel_codex_task"}:
            return await self._codex_agent.execute_tool(name, arguments)
        if self._timer_tool is not None and name in {"start_timer", "get_timers", "cancel_timer"}:
            return await self._timer_tool.execute_tool(name, arguments)
        return await self._ha_tools.execute_tool(name, arguments)
