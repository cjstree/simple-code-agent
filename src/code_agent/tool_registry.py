"""Registration and permission enforcement for agent tools."""

from collections.abc import Iterable
from typing import Any

from code_agent.tool import Tool


class ToolRegistry:
    """Store tools and enforce tool-name based permission policies."""

    def __init__(
        self,
        tools: list[Tool],
        *,
        blocked_tools: Iterable[str] = (),
        sensitive_tools: Iterable[str] = (),
    ) -> None:
        self.tools: dict[str, Tool] = {}
        self.blocked_tools = frozenset(blocked_tools)
        self.sensitive_tools = frozenset(sensitive_tools)
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        if tool.name in self.tools:
            raise ValueError(
                f"Tool {tool.name} exists. Register the tool with another name."
            )
        self.tools[tool.name] = tool

    def requires_approval(self, tool_name: str) -> bool:
        """Return whether a visible tool requires approval for each call."""
        return tool_name in self.sensitive_tools and tool_name not in self.blocked_tools

    async def run_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        approved: bool = False,
    ) -> str:
        if tool_name not in self.tools:
            raise LookupError(f"Tool {tool_name} does not exist. Register it first.")
        if tool_name in self.blocked_tools:
            raise PermissionError(f"Tool {tool_name} is blocked.")
        if self.requires_approval(tool_name) and not approved:
            raise PermissionError(f"Tool {tool_name} requires approval.")
        return await self.tools[tool_name].run(arguments)

    def get_tools_desc(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in self.tools.values()
            if tool.name not in self.blocked_tools
        ]
