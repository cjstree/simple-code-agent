from types import SimpleNamespace
from typing import Any, Self

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
async def test_agent_trigger_hook_awaits_async_callbacks_in_order() -> None:
    agent = Agent(telemetry=AgentTelemetry())
    calls: list[str] = []

    def sync_hook() -> None:
        calls.append("sync")

    async def async_hook() -> None:
        calls.append("async")

    agent.registHook("PreLLMSubmit", sync_hook)
    agent.registHook("PreLLMSubmit", async_hook)

    await agent.triggerHook("PreLLMSubmit")

    assert calls == ["sync", "async"]


@pytest.mark.asyncio
async def test_agent_starts_with_local_tools_and_no_mcp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_model_name", "test-model")
    monkeypatch.setattr(settings, "mcp_url", None)
    agent = Agent(telemetry=AgentTelemetry())

    try:
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
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_agent_run_can_be_called_without_cli_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_model_name", "test-model")
    monkeypatch.setattr(settings, "mcp_url", None)
    agent = Agent(telemetry=AgentTelemetry(), system_prompt="test system")
    loop_calls = 0

    async def fake_agent_loop() -> None:
        nonlocal loop_calls
        loop_calls += 1

    try:
        await agent.start()
        monkeypatch.setattr(agent, "_agent_loop", fake_agent_loop)

        result = await agent.run("hello")

        assert result is not None
        assert loop_calls == 1
        assert agent.messages == [
            {"role": "system", "content": "test system"},
            {"role": "user", "content": "hello"},
        ]
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_agent_keeps_mcp_open_until_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_model_name", "test-model")
    monkeypatch.setattr(settings, "mcp_url", "https://mcp.example.test")

    class FakeMCPClient:
        def __init__(self, *, url: str | None, connect_timeout: float) -> None:
            self.url = url
            self.connect_timeout = connect_timeout
            self.entered = False
            self.exited = False

        async def __aenter__(self) -> Self:
            self.entered = True
            return self

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            self.exited = True

        async def list_tools(self) -> list[Any]:
            return []

    monkeypatch.setattr("code_agent.agent.MCPClient", FakeMCPClient)
    agent = Agent(telemetry=AgentTelemetry())

    await agent.start()
    mcp_client = agent.mcp_client

    assert isinstance(mcp_client, FakeMCPClient)
    assert mcp_client.entered is True
    assert mcp_client.exited is False

    await agent.close()

    assert mcp_client.exited is True
    assert agent.mcp_client is None


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_error", [EOFError, KeyboardInterrupt])
async def test_agent_exits_cleanly_on_terminal_signal(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    exit_error: type[BaseException],
) -> None:
    async def raise_exit_error(prompt: str) -> str:
        raise exit_error

    monkeypatch.setattr("code_agent.agent.separator", lambda: "---")
    monkeypatch.setattr("code_agent.agent.ainput", raise_exit_error)

    agent = Agent(telemetry=AgentTelemetry())
    agent.memory = SimpleNamespace(extract_memories=lambda messages: None)
    await agent.cli_loop()

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

    agent = Agent(telemetry=AgentTelemetry())
    registry = FakeRegistry()
    agent.tool_registry = registry  # type: ignore[assignment]
    agent.context_manager = ContextManager(object())
    agent.messages = [{"role": "user", "content": "run the tool"}]
    agent.system_prompt = "test"
    agent.max_tool_round = 3

    async def call_api(messages: list[dict[str, Any]]) -> Any:
        return next(responses)

    agent._call_api = call_api  # type: ignore[method-assign]

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
async def test_agent_reads_sensitive_tool_decision(
    monkeypatch: pytest.MonkeyPatch,
    decision: str,
    approved: bool,
) -> None:
    async def read_decision(prompt: str) -> str:
        return decision

    monkeypatch.setattr("code_agent.agent.ainput", read_decision)

    agent = Agent(telemetry=AgentTelemetry())

    assert await agent._ask_permission("write") is approved
