"""Runtime tool registry for Home Assistant and web tools."""

from __future__ import annotations

from typing import Any

from ..ha_tools.client import HomeAssistantToolBridge
from .web_search import WebSearchTool


class ToolRegistry:
    def __init__(self, ha_tools: HomeAssistantToolBridge, web_search: WebSearchTool) -> None:
        self._ha_tools = ha_tools
        self._web_search = web_search
        self._enabled_tools = {
            "get_entities": True,
            "get_state": True,
            "call_service": True,
            "web_search": True,
        }

    async def close(self) -> None:
        await self._ha_tools.close()
        await self._web_search.close()

    def set_enabled_tools(self, enabled_tools: dict[str, bool]) -> None:
        self._enabled_tools.update(enabled_tools)

    def tool_definitions(self) -> list[dict[str, Any]]:
        definitions = []
        for definition in self._ha_tools.tool_definitions():
            if self._enabled_tools.get(str(definition["name"]), True):
                definitions.append(definition)
        if self._enabled_tools.get("web_search", True):
            definitions.append(self._web_search.tool_definition())
        return definitions

    async def execute_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self._enabled_tools.get(name, True):
            return {"error": f"Tool disabled: {name}"}
        if name == "web_search":
            return await self._web_search.execute(arguments)
        return await self._ha_tools.execute_tool(name, arguments)
