"""Adapter for tools discovered from an MCP server."""

import json
from dataclasses import dataclass
from typing import Any, ClassVar

from mcp.client.session import ClientSession


@dataclass(slots=True)
class MCPTool:
    """Expose one remote MCP tool through the local Tool interface."""

    session: ClientSession
    name: str
    description: str
    parameters: dict[str, Any]

    type: ClassVar[str] = "function"

    @classmethod
    async def discover(cls, session: ClientSession) -> list["MCPTool"]:
        """Discover the tools exposed by an initialized MCP session."""
        response = await session.list_tools()
        return [
            cls(
                session=session,
                name=tool.name,
                description=tool.description or "",
                parameters=tool.input_schema,
            )
            for tool in response.tools
        ]

    async def run(self, arguments: dict[str, Any]) -> str:
        """Call this tool on its MCP server and return a textual result."""
        result = await self.session.call_tool(
            name=self.name,
            arguments=arguments,
        )
        return json.dumps(
            result.structured_content,
            ensure_ascii=False,
            indent=2,
        )
