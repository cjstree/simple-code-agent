from typing import Any, ClassVar

import pytest

from code_agent.tool_registry import ToolRegistry


class EchoTool:
    type = "function"
    name = "echo"
    description = "Return the supplied text"
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    async def run(self, arguments: dict[str, Any]) -> str:
        return str(arguments["text"])


@pytest.mark.asyncio
async def test_registry_describes_and_runs_registered_tool() -> None:
    registry = ToolRegistry(tools=[EchoTool()])

    assert await registry.run_tool("echo", {"text": "hello"}) == "hello"
    assert registry.get_tools_desc() == [
        {
            "type": "function",
            "function": {
                "name": "echo",
                "description": "Return the supplied text",
                "parameters": EchoTool.parameters,
            },
        }
    ]


def test_registry_rejects_duplicate_tool_names() -> None:
    with pytest.raises(ValueError, match="echo"):
        ToolRegistry(tools=[EchoTool(), EchoTool()])


@pytest.mark.asyncio
async def test_registry_rejects_unknown_tool() -> None:
    registry = ToolRegistry(tools=[])

    with pytest.raises(LookupError, match="missing"):
        await registry.run_tool("missing", {})


@pytest.mark.asyncio
async def test_registry_hides_and_rejects_blocked_tool() -> None:
    registry = ToolRegistry(tools=[EchoTool()], blocked_tools={"echo"})

    assert registry.get_tools_desc() == []
    with pytest.raises(PermissionError, match="blocked"):
        await registry.run_tool("echo", {"text": "hello"}, approved=True)


@pytest.mark.asyncio
async def test_registry_requires_per_call_approval_for_sensitive_tool() -> None:
    registry = ToolRegistry(tools=[EchoTool()], sensitive_tools={"echo"})

    assert registry.get_tools_desc()[0]["function"]["name"] == "echo"
    assert registry.requires_approval("echo") is True
    with pytest.raises(PermissionError, match="requires approval"):
        await registry.run_tool("echo", {"text": "hello"})
    assert await registry.run_tool("echo", {"text": "hello"}, approved=True) == "hello"
