"""Adapter for tools discovered from an MCP server."""

import json
from dataclasses import dataclass
from typing import Any, ClassVar

from code_agent.mcp_client import MCPClient


@dataclass(slots=True)
class MCPTool:
    """Expose one remote MCP tool through the local Tool interface."""

    client: MCPClient
    name: str
    description: str
    parameters: dict[str, Any]

    type: ClassVar[str] = "function"

    @classmethod
    async def discover(cls, client: MCPClient) -> list["MCPTool"]:
        """Discover the tools exposed by an initialized MCP client."""
        tools = await client.list_tools()
        return [
            cls(
                client=client,
                name=tool.name,
                description=tool.description or "",
                parameters=tool.input_schema,
            )
            for tool in tools
        ]

    async def run(self, arguments: dict[str, Any]) -> str:
        """Call this tool on its MCP server and return a textual result."""
        result = await self.client.call_tool(
            name=self.name,
            arguments=arguments,
        )
        return json.dumps(
            result.structured_content,
            ensure_ascii=False,
            indent=2,
        )
