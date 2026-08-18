import json
from types import SimpleNamespace
from typing import Any

import pytest

from code_agent.mcp_tool import MCPTool


class FakeMCPClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_tools(self) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(
                name="search_knowledge",
                description="Search the knowledge base",
                input_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            )
        ]

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> SimpleNamespace:
        self.calls.append((name, arguments))
        return SimpleNamespace(structured_content={"matches": ["result"]})


@pytest.mark.asyncio
async def test_mcp_tool_discovery_and_call_share_the_client() -> None:
    client = FakeMCPClient()

    tools = await MCPTool.discover(client)  # type: ignore[arg-type]
    result = await tools[0].run({"query": "Transformer"})

    assert len(tools) == 1
    assert tools[0].client is client
    assert tools[0].name == "search_knowledge"
    assert tools[0].parameters["required"] == ["query"]
    assert client.calls == [("search_knowledge", {"query": "Transformer"})]
    assert json.loads(result) == {"matches": ["result"]}
