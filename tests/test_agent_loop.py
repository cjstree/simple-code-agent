from types import SimpleNamespace
from typing import Any

import pytest

from code_agent.agent import Agent
from code_agent.context_manager import ContextManager
from code_agent.settings import settings
from code_agent.telemetry import AgentTelemetry


class FakeRegistry:
    def __init__(self, *, sensitive: bool = False) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.approvals: list[bool] = []
        self.sensitive = sensitive

    def requires_approval(self, tool_name: str) -> bool:
        return self.sensitive

    async def run_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        approved: bool = False,
    ) -> str:
        self.calls.append((tool_name, arguments))
        self.approvals.append(approved)
        if self.sensitive and not approved:
            raise PermissionError(f"Tool {tool_name} requires approval.")
        return "result: 2"


@pytest.mark.asyncio
async def test_agent_starts_with_local_tools_and_no_mcp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_model_name", "test-model")
    monkeypatch.setattr(settings, "mcp_url", None)
    agent = Agent(telemetry=AgentTelemetry())

    async def stop_before_interactive_loop() -> None:
        return None

    monkeypatch.setattr(agent, "_loop", stop_before_interactive_loop)

    await agent.start()

    assert set(agent.tool_registry.tools) == {
        "bash",
        "edit",
        "glob",
        "grep",
        "load_skills",
        "read",
        "todo_write",
        "write",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_error", [EOFError, KeyboardInterrupt])
async def test_agent_exits_cleanly_on_terminal_signal(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    exit_error: type[BaseException],
) -> None:
    def raise_exit_error(prompt: str) -> str:
        raise exit_error

    monkeypatch.setattr("code_agent.agent.separator", lambda: "---")
    monkeypatch.setattr("builtins.input", raise_exit_error)

    agent = Agent()
    await agent._loop()

    assert "Goodbye!" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_agent_completes_one_tool_call_cycle() -> None:
    tool_call = SimpleNamespace(
        id="call-1",
        type="function",
        function=SimpleNamespace(
            name="echo",
            arguments='{"value": 2}',
        ),
    )
    responses = iter(
        [
            SimpleNamespace(content=None, tool_calls=[tool_call]),
            SimpleNamespace(content="finished", tool_calls=None),
        ]
    )

    agent = Agent()
    registry = FakeRegistry()
    agent.tool_registry = registry  # type: ignore[assignment]
    agent.context_manager = ContextManager(object())
    agent.messages = [{"role": "user", "content": "run the tool"}]
    agent.system_prompt = "test"
    agent.max_tool_round = 3
    agent._call_api = lambda messages, system_prompt: next(responses)  # type: ignore[method-assign]

    await agent._agent_loop()

    assert registry.calls == [("echo", {"value": 2})]
    assert [message["role"] for message in agent.messages] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]
    assert agent.messages[1]["tool_calls"][0]["id"] == "call-1"
    assert agent.messages[2] == {
        "role": "tool",
        "tool_call_id": "call-1",
        "content": "result: 2",
    }
    assert agent.messages[-1]["content"] == "finished"


@pytest.mark.asyncio
@pytest.mark.parametrize(("decision", "approved"), [("yes", True), ("", False)])
async def test_agent_passes_sensitive_tool_decision_to_registry(
    monkeypatch: pytest.MonkeyPatch,
    decision: str,
    approved: bool,
) -> None:
    tool_call = SimpleNamespace(
        id="call-1",
        type="function",
        function=SimpleNamespace(name="write", arguments='{"path": "note.txt"}'),
    )
    responses = iter(
        [
            SimpleNamespace(content=None, tool_calls=[tool_call]),
            SimpleNamespace(content="finished", tool_calls=None),
        ]
    )
    monkeypatch.setattr("builtins.input", lambda prompt: decision)

    agent = Agent()
    registry = FakeRegistry(sensitive=True)
    agent.tool_registry = registry  # type: ignore[assignment]
    agent.context_manager = ContextManager(object())
    agent.messages = [{"role": "user", "content": "write a note"}]
    agent.system_prompt = "test"
    agent.max_tool_round = 3
    agent._call_api = lambda messages, system_prompt: next(responses)  # type: ignore[method-assign]

    await agent._agent_loop()

    assert registry.approvals == [approved]
    assert agent.messages[2]["role"] == "tool"
    if approved:
        assert agent.messages[2]["content"] == "result: 2"
    else:
        assert "requires approval" in agent.messages[2]["content"]
