import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Self

import pytest
from openai.types.chat import (
    ChatCompletion,
    ChatCompletionChunk,
    ChatCompletionMessage,
    ChatCompletionMessageFunctionToolCall,
)
from openai.types.chat.chat_completion import Choice
from openai.types.chat.chat_completion_chunk import Choice as ChunkChoice
from openai.types.chat.chat_completion_chunk import ChoiceDelta
from openai.types.chat.chat_completion_message_function_tool_call import Function
from openai.types.completion_usage import CompletionUsage

from code_agent.agent import Agent
from code_agent.session import Session
from code_agent.settings import settings
from code_agent.telemetry import AgentTelemetry


class FakeRegistry:
    def __init__(self, *, sensitive: bool = False) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.approvals: list[bool] = []
        self.sensitive = sensitive

    def requires_approval(self, tool_name: str) -> bool:
        return self.sensitive

    def get_tools_desc(self) -> list[dict[str, Any]]:
        return []

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


class FakeChatCompletionStream:
    def __init__(self, *chunks: Any) -> None:
        self._chunks = iter(chunks)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None

    async def __aiter__(self):
        for chunk in self._chunks:
            yield chunk


def stream_chunk(
    *,
    delta: dict[str, Any] | None = None,
    finish_reason: str | None = None,
    usage: CompletionUsage | None = None,
) -> ChatCompletionChunk:
    choices = (
        [
            ChunkChoice(
                delta=ChoiceDelta.model_validate(delta),
                finish_reason=finish_reason,
                index=0,
            )
        ]
        if delta is not None
        else []
    )
    return ChatCompletionChunk(
        id="completion-1",
        choices=choices,
        created=123,
        model="test-model",
        object="chat.completion.chunk",
        service_tier=None,
        system_fingerprint="fingerprint-1",
        usage=usage,
    )


def chat_completion(
    message: ChatCompletionMessage,
    *,
    finish_reason: str,
    usage: CompletionUsage | None = None,
) -> ChatCompletion:
    return ChatCompletion(
        id="completion-1",
        choices=[Choice(index=0, message=message, finish_reason=finish_reason)],
        created=123,
        model="test-model",
        object="chat.completion",
        usage=usage,
    )


class RecordingTurnSpan:
    def __init__(self) -> None:
        self.output: Any = None
        self.attributes: dict[str, Any] = {}

    def set_output(self, output: Any) -> None:
        self.output = output

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value


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
async def test_agent_trigger_hook_stops_after_first_returned_result() -> None:
    # A hook result short-circuits later callbacks and is returned to the caller.
    agent = Agent(telemetry=AgentTelemetry())
    calls: list[str] = []

    def allow_hook() -> None:
        calls.append("allow")

    async def deny_hook() -> bool:
        calls.append("deny")
        return False

    def skipped_hook() -> None:
        calls.append("skipped")

    agent.registHook("PreToolUse", allow_hook)
    agent.registHook("PreToolUse", deny_hook)
    agent.registHook("PreToolUse", skipped_hook)

    result = await agent.triggerHook("PreToolUse")

    assert result is False
    assert calls == ["allow", "deny"]


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
    completion = chat_completion(
        ChatCompletionMessage(role="assistant", content="finished"),
        finish_reason="stop",
    )

    async def fake_agent_loop() -> ChatCompletion:
        nonlocal loop_calls
        loop_calls += 1
        return completion

    try:
        await agent.start()
        monkeypatch.setattr(agent, "_agent_loop", fake_agent_loop)

        result = await agent.run("hello")

        assert result == "finished"
        assert loop_calls == 1
        assert agent.session is not None
        assert agent.session.build_context() == [
            {"role": "system", "content": "test system"},
            {"role": "user", "content": "hello"},
        ]
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_agent_uses_session_for_request_and_response_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A started agent stores both sides of a turn in Session-built context.
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_model_name", "test-model")
    monkeypatch.setattr(settings, "mcp_url", None)
    agent = Agent(telemetry=AgentTelemetry(), system_prompt="test system")
    completion = chat_completion(
        ChatCompletionMessage(role="assistant", content="finished"),
        finish_reason="stop",
        usage=CompletionUsage(
            completion_tokens=2,
            prompt_tokens=3,
            total_tokens=5,
        ),
    )
    request_contexts: list[list[dict[str, Any]]] = []

    async def call_api(messages: list[dict[str, Any]]) -> ChatCompletion:
        request_contexts.append(messages)
        return completion

    async def extract_memories(messages: list[dict[str, Any]]) -> None:
        return None

    async def select_relevant_memories(
        messages: list[dict[str, Any]],
    ) -> list[str]:
        return []

    try:
        await agent.start()
        agent.memory = SimpleNamespace(
            extract_memories=extract_memories,
            select_relevant_memories=select_relevant_memories,
        )
        monkeypatch.setattr(agent, "_call_api", call_api)

        result = await agent.run("hello")

        assert result == "finished"
        assert request_contexts == [
            [
                {"role": "system", "content": "test system"},
                {"role": "user", "content": "hello"},
            ]
        ]
        assert agent.session is not None
        assert [entry.role for entry in agent.session.entrys] == [
            "user",
            "assistant",
        ]
        assert not hasattr(agent, "messages")
        assert agent.session.build_context()[-1] == {
            "role": "assistant",
            "content": "finished",
        }
        assert agent.session.context_token == 5
    finally:
        await agent.close()


# Consecutive programmatic turns can opt into one telemetry session while the
# public run method continues to create a new session by default.
@pytest.mark.asyncio
async def test_agent_run_can_reuse_or_create_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_model_name", "test-model")
    monkeypatch.setattr(settings, "mcp_url", None)
    agent = Agent(telemetry=AgentTelemetry(), system_prompt="test system")
    completion = chat_completion(
        ChatCompletionMessage(role="assistant", content="finished"),
        finish_reason="stop",
    )

    async def fake_agent_loop() -> ChatCompletion:
        return completion

    try:
        await agent.start()
        monkeypatch.setattr(agent, "_agent_loop", fake_agent_loop)

        await agent.run("first")
        first_session = agent.session_id
        await agent.run("second", new_session=False)
        assert agent.session_id == first_session

        await agent.run("third")
        assert agent.session_id != first_session
    finally:
        await agent.close()


# The Stop hook awaits memory extraction so writes complete before the turn
# returns to a multi-turn evaluation runner.
@pytest.mark.asyncio
async def test_extract_memory_awaits_memory_backend() -> None:
    agent = Agent(telemetry=AgentTelemetry())
    agent.session = Session(
        sys_prompt="test system",
        client=object(),  # type: ignore[arg-type]
    )
    agent.session.append_message({"role": "user", "content": "remember this"})
    extracted_messages: list[list[dict[str, str]]] = []

    async def extract_memories(messages: list[dict[str, str]]) -> None:
        extracted_messages.append(messages)

    agent.memory = SimpleNamespace(extract_memories=extract_memories)

    await agent.extract_memory()

    assert extracted_messages == [agent.session.build_context()]


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
@pytest.mark.parametrize("failure_type", [RuntimeError, asyncio.CancelledError])
async def test_agent_releases_mcp_when_startup_does_not_complete(
    monkeypatch: pytest.MonkeyPatch,
    failure_type: type[BaseException],
) -> None:
    # MCP resources are released when discovery fails or startup is cancelled.
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_model_name", "test-model")
    monkeypatch.setattr(settings, "mcp_url", "https://mcp.example.test")
    clients: list[Any] = []

    class FakeMCPClient:
        def __init__(self, *, url: str | None, connect_timeout: float) -> None:
            self.url = url
            self.connect_timeout = connect_timeout
            self.entered = False
            self.exit_count = 0
            clients.append(self)

        async def __aenter__(self) -> Self:
            self.entered = True
            return self

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            self.exit_count += 1

    async def fail_discovery(client: FakeMCPClient) -> list[Any]:
        del client
        raise failure_type("tool discovery did not complete")

    monkeypatch.setattr("code_agent.agent.MCPClient", FakeMCPClient)
    monkeypatch.setattr(
        "code_agent.agent.MCPTool",
        SimpleNamespace(discover=fail_discovery),
    )
    agent = Agent(telemetry=AgentTelemetry())

    with pytest.raises(failure_type, match="tool discovery did not complete"):
        await agent.start()

    assert len(clients) == 1
    assert clients[0].entered is True
    assert clients[0].exit_count == 1
    assert agent.mcp_client is None

    await agent.close()
    assert clients[0].exit_count == 1


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
async def test_cli_loop_explicitly_enables_streaming(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = iter(["hello", "exit"])
    stream_values: list[bool] = []

    async def fake_input(prompt: str) -> str:
        del prompt
        return next(inputs)

    async def fake_agent_loop(*, stream: bool = False) -> None:
        stream_values.append(stream)

    monkeypatch.setattr("code_agent.agent.ainput", fake_input)
    agent = Agent(telemetry=AgentTelemetry())
    agent.session = Session(
        sys_prompt="test system",
        client=object(),  # type: ignore[arg-type]
    )
    monkeypatch.setattr(agent, "_agent_loop", fake_agent_loop)

    await agent.cli_loop()

    assert stream_values == [True]


@pytest.mark.asyncio
async def test_streaming_prints_raw_text_once_and_stores_complete_message(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Streaming returns one standard completion while preserving terminal output.
    completion_stream = FakeChatCompletionStream(
        stream_chunk(
            delta={"role": "assistant", "content": "**bo"},
        ),
        stream_chunk(
            delta={"content": "ld**"},
            finish_reason="stop",
        ),
        stream_chunk(
            usage=CompletionUsage(
                completion_tokens=2,
                prompt_tokens=3,
                total_tokens=5,
            ),
        ),
    )
    calls: list[dict[str, Any]] = []

    class FakeCompletions:
        async def create(self, **kwargs: Any) -> FakeChatCompletionStream:
            calls.append(kwargs)
            return completion_stream

    agent = Agent(telemetry=AgentTelemetry())
    agent.client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    non_strict_tools = [
        {
            "type": "function",
            "function": {
                "name": "read",
                "description": "Read a file",
                "parameters": {"type": "object"},
            },
        }
    ]
    agent.tool_registry = SimpleNamespace(
        get_tools_desc=lambda: non_strict_tools
    )
    agent.session = Session(
        sys_prompt="test system",
        client=agent.client,  # type: ignore[arg-type]
    )
    agent.session.append_message({"role": "user", "content": "hello"})
    agent.max_tool_round = 1

    completion = await agent._agent_loop(stream=True)

    output = capsys.readouterr().out
    assert output.count("**bold**") == 1
    assert agent.session.build_context()[-1] == {
        "role": "assistant",
        "content": "**bold**",
    }
    assert calls[0]["stream_options"] == {"include_usage": True}
    assert calls[0]["stream"] is True
    assert calls[0]["tools"] == non_strict_tools
    assert completion.choices[0].message.content == "**bold**"
    assert completion.choices[0].finish_reason == "stop"
    assert completion.usage is not None
    assert completion.usage.total_tokens == 5


@pytest.mark.asyncio
async def test_streaming_reassembles_fragmented_tool_calls() -> None:
    # Fragmented tool-call deltas are assembled into a standard completion.
    completion_stream = FakeChatCompletionStream(
        stream_chunk(
            delta={
                "role": "assistant",
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "echo", "arguments": '{"val'},
                    }
                ],
            },
        ),
        stream_chunk(
            delta={
                "tool_calls": [
                    {
                        "index": 0,
                        "function": {"arguments": 'ue": 2}'},
                    }
                ],
            },
            finish_reason="tool_calls",
        ),
    )

    class FakeCompletions:
        async def create(self, **kwargs: Any) -> FakeChatCompletionStream:
            return completion_stream

    agent = Agent(telemetry=AgentTelemetry())
    agent.client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    agent.tool_registry = FakeRegistry()  # type: ignore[assignment]

    response = await agent._call_api([], stream=True)

    message = response.choices[0].message
    assert response.choices[0].finish_reason == "tool_calls"
    assert message.content is None
    assert message.tool_calls is not None
    assert message.tool_calls[0].id == "call-1"
    assert message.tool_calls[0].function.name == "echo"
    assert message.tool_calls[0].function.arguments == '{"value": 2}'


@pytest.mark.asyncio
async def test_agent_completes_one_tool_call_cycle() -> None:
    tool_call = ChatCompletionMessageFunctionToolCall(
        id="call-1",
        type="function",
        function=Function(
            name="echo",
            arguments='{"value": 2}',
        ),
    )
    responses = iter(
        [
            chat_completion(
                ChatCompletionMessage(
                    role="assistant", content=None, tool_calls=[tool_call]
                ),
                finish_reason="tool_calls",
            ),
            chat_completion(
                ChatCompletionMessage(role="assistant", content="finished"),
                finish_reason="stop",
            ),
        ]
    )

    agent = Agent(telemetry=AgentTelemetry())
    registry = FakeRegistry()
    agent.tool_registry = registry  # type: ignore[assignment]
    agent.system_prompt = "test"
    agent.session = Session(
        sys_prompt=agent.system_prompt,
        client=object(),  # type: ignore[arg-type]
    )
    agent.session.append_message(
        {"role": "user", "content": "run the tool"}
    )
    agent.max_tool_round = 3

    async def call_api(messages: list[dict[str, Any]]) -> Any:
        return next(responses)

    agent._call_api = call_api  # type: ignore[method-assign]

    await agent._agent_loop()

    assert registry.calls == [("echo", {"value": 2})]
    messages = agent.session.build_context()
    assert [message["role"] for message in messages] == [
        "system",
        "user",
        "assistant",
        "tool",
        "assistant",
    ]
    assert messages[2]["tool_calls"][0]["id"] == "call-1"
    assert messages[3] == {
        "role": "tool",
        "tool_call_id": "call-1",
        "content": "result: 2",
    }
    assert messages[-1]["content"] == "finished"


@pytest.mark.asyncio
async def test_agent_records_invalid_tool_arguments_before_continuing() -> None:
    # Invalid tool JSON becomes an ordered tool result visible to the next request.
    tool_call = ChatCompletionMessageFunctionToolCall(
        id="call-invalid-json",
        type="function",
        function=Function(name="echo", arguments='{"value":'),
    )
    responses = iter(
        [
            chat_completion(
                ChatCompletionMessage(
                    role="assistant", content=None, tool_calls=[tool_call]
                ),
                finish_reason="tool_calls",
            ),
            chat_completion(
                ChatCompletionMessage(role="assistant", content="recovered"),
                finish_reason="stop",
            ),
        ]
    )
    request_contexts: list[list[dict[str, Any]]] = []
    registry = FakeRegistry()
    agent = Agent(telemetry=AgentTelemetry())
    agent.tool_registry = registry  # type: ignore[assignment]
    agent.session = Session(
        sys_prompt="test system",
        client=object(),  # type: ignore[arg-type]
    )
    agent.session.append_message({"role": "user", "content": "run the tool"})
    agent.max_tool_round = 2

    async def call_api(messages: list[dict[str, Any]]) -> ChatCompletion:
        request_contexts.append(messages)
        return next(responses)

    agent._call_api = call_api  # type: ignore[method-assign]

    completion = await agent._agent_loop()

    assert completion.choices[0].message.content == "recovered"
    assert registry.calls == []
    tool_result = request_contexts[1][-1]
    assert tool_result["role"] == "tool"
    assert tool_result["tool_call_id"] == "call-invalid-json"
    assert tool_result["content"].startswith("error: invalid JSON tool arguments:")


@pytest.mark.asyncio
async def test_agent_records_tool_failure_before_continuing() -> None:
    # A tool exception becomes a matching tool result visible to the next request.
    tool_call = ChatCompletionMessageFunctionToolCall(
        id="call-failed",
        type="function",
        function=Function(name="echo", arguments='{"value": 2}'),
    )
    responses = iter(
        [
            chat_completion(
                ChatCompletionMessage(
                    role="assistant", content=None, tool_calls=[tool_call]
                ),
                finish_reason="tool_calls",
            ),
            chat_completion(
                ChatCompletionMessage(role="assistant", content="recovered"),
                finish_reason="stop",
            ),
        ]
    )
    request_contexts: list[list[dict[str, Any]]] = []

    class FailingRegistry(FakeRegistry):
        async def run_tool(
            self,
            tool_name: str,
            arguments: dict[str, Any],
            *,
            approved: bool = False,
        ) -> str:
            self.calls.append((tool_name, arguments))
            self.approvals.append(approved)
            raise OSError("tool backend unavailable")

    registry = FailingRegistry()
    agent = Agent(telemetry=AgentTelemetry())
    agent.tool_registry = registry  # type: ignore[assignment]
    agent.session = Session(
        sys_prompt="test system",
        client=object(),  # type: ignore[arg-type]
    )
    agent.session.append_message({"role": "user", "content": "run the tool"})
    agent.max_tool_round = 2

    async def call_api(messages: list[dict[str, Any]]) -> ChatCompletion:
        request_contexts.append(messages)
        return next(responses)

    agent._call_api = call_api  # type: ignore[method-assign]

    completion = await agent._agent_loop()

    assert completion.choices[0].message.content == "recovered"
    assert registry.calls == [("echo", {"value": 2})]
    tool_result = request_contexts[1][-1]
    assert tool_result["role"] == "tool"
    assert tool_result["tool_call_id"] == "call-failed"
    assert tool_result["content"].startswith("error: tool run error:")
    assert "tool backend unavailable" in tool_result["content"]


@pytest.mark.asyncio
async def test_agent_preserves_order_when_one_of_multiple_tools_fails() -> None:
    # A failed tool does not prevent later calls, and both results retain call order.
    tool_calls = [
        ChatCompletionMessageFunctionToolCall(
            id="call-first",
            type="function",
            function=Function(name="first", arguments="{}"),
        ),
        ChatCompletionMessageFunctionToolCall(
            id="call-second",
            type="function",
            function=Function(name="second", arguments="{}"),
        ),
    ]
    responses = iter(
        [
            chat_completion(
                ChatCompletionMessage(
                    role="assistant", content=None, tool_calls=tool_calls
                ),
                finish_reason="tool_calls",
            ),
            chat_completion(
                ChatCompletionMessage(role="assistant", content="finished"),
                finish_reason="stop",
            ),
        ]
    )
    request_contexts: list[list[dict[str, Any]]] = []

    class MixedRegistry(FakeRegistry):
        async def run_tool(
            self,
            tool_name: str,
            arguments: dict[str, Any],
            *,
            approved: bool = False,
        ) -> str:
            self.calls.append((tool_name, arguments))
            self.approvals.append(approved)
            if tool_name == "first":
                raise RuntimeError("first failed")
            return "second succeeded"

    registry = MixedRegistry()
    agent = Agent(telemetry=AgentTelemetry())
    agent.tool_registry = registry  # type: ignore[assignment]
    agent.session = Session(
        sys_prompt="test system",
        client=object(),  # type: ignore[arg-type]
    )
    agent.session.append_message({"role": "user", "content": "run both tools"})
    agent.max_tool_round = 2

    async def call_api(messages: list[dict[str, Any]]) -> ChatCompletion:
        request_contexts.append(messages)
        return next(responses)

    agent._call_api = call_api  # type: ignore[method-assign]

    await agent._agent_loop()

    assert registry.calls == [("first", {}), ("second", {})]
    visible_results = request_contexts[1][-2:]
    assert [result["tool_call_id"] for result in visible_results] == [
        "call-first",
        "call-second",
    ]
    assert "first failed" in visible_results[0]["content"]
    assert visible_results[1]["content"] == "second succeeded"


@pytest.mark.asyncio
async def test_agent_records_denied_sensitive_tool_before_continuing() -> None:
    # A denied sensitive call remains visible as a tool result before continuing.
    tool_call = ChatCompletionMessageFunctionToolCall(
        id="call-denied",
        type="function",
        function=Function(name="write", arguments='{"path": "notes.txt"}'),
    )
    responses = iter(
        [
            chat_completion(
                ChatCompletionMessage(
                    role="assistant", content=None, tool_calls=[tool_call]
                ),
                finish_reason="tool_calls",
            ),
            chat_completion(
                ChatCompletionMessage(role="assistant", content="not written"),
                finish_reason="stop",
            ),
        ]
    )
    request_contexts: list[list[dict[str, Any]]] = []
    registry = FakeRegistry(sensitive=True)
    agent = Agent(telemetry=AgentTelemetry())
    agent.tool_registry = registry  # type: ignore[assignment]
    agent.session = Session(
        sys_prompt="test system",
        client=object(),  # type: ignore[arg-type]
    )
    agent.session.append_message({"role": "user", "content": "write a note"})
    agent.max_tool_round = 2

    async def deny_permission(tool_name: str) -> bool:
        assert tool_name == "write"
        return False

    async def call_api(messages: list[dict[str, Any]]) -> ChatCompletion:
        request_contexts.append(messages)
        return next(responses)

    agent._ask_permission = deny_permission  # type: ignore[method-assign]
    agent.registHook("PreToolUse", agent.check_tool_permission)
    agent._call_api = call_api  # type: ignore[method-assign]

    completion = await agent._agent_loop()

    assert completion.choices[0].message.content == "not written"
    assert registry.calls == [("write", {"path": "notes.txt"})]
    assert registry.approvals == [False]
    tool_result = request_contexts[1][-1]
    assert tool_result["role"] == "tool"
    assert tool_result["tool_call_id"] == "call-denied"
    assert "requires approval" in tool_result["content"]


@pytest.mark.asyncio
async def test_agent_stops_after_maximum_tool_rounds_with_result_recorded(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The round limit stops another model request after recording the last result.
    tool_call = ChatCompletionMessageFunctionToolCall(
        id="call-limit",
        type="function",
        function=Function(name="echo", arguments='{"value": 2}'),
    )
    response = chat_completion(
        ChatCompletionMessage(role="assistant", content=None, tool_calls=[tool_call]),
        finish_reason="tool_calls",
    )
    request_count = 0
    stop_contexts: list[list[dict[str, Any]]] = []
    registry = FakeRegistry()
    agent = Agent(telemetry=AgentTelemetry())
    agent.tool_registry = registry  # type: ignore[assignment]
    agent.session = Session(
        sys_prompt="test system",
        client=object(),  # type: ignore[arg-type]
    )
    agent.session.append_message({"role": "user", "content": "keep using tools"})
    agent.max_tool_round = 1

    async def call_api(messages: list[dict[str, Any]]) -> ChatCompletion:
        nonlocal request_count
        request_count += 1
        return response

    def observe_stop() -> None:
        stop_contexts.append(agent.session.build_context())

    agent._call_api = call_api  # type: ignore[method-assign]
    agent.registHook("Stop", observe_stop)

    completion = await agent._agent_loop()

    assert completion is response
    assert request_count == 1
    assert registry.calls == [("echo", {"value": 2})]
    assert stop_contexts[0][-1] == {
        "role": "tool",
        "tool_call_id": "call-limit",
        "content": "result: 2",
    }
    assert "Maximum tool-call rounds reached." in capsys.readouterr().out


@pytest.mark.asyncio
async def test_agent_builds_dynamic_system_prompt_and_injects_memory(
    tmp_path: Path,
) -> None:
    # Skills remain in the stable system prompt while memory becomes user context.
    selected_contexts: list[list[dict[str, Any]]] = []
    agent = Agent(telemetry=AgentTelemetry())
    skill_dir = tmp_path / "reviewing"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: reviewing\ndescription: Review Python code\n---\nInstructions",
        encoding="utf-8",
    )
    agent.skill_registry.scan(tmp_path)
    agent.session = Session(
        sys_prompt="",
        client=object(),  # type: ignore[arg-type]
    )
    agent.session.append_message({"role": "user", "content": "review this"})

    async def select_relevant_memories(
        messages: list[dict[str, Any]],
    ) -> list[str]:
        selected_contexts.append(messages)
        return ["Prefer focused tests."]

    agent.memory = SimpleNamespace(select_relevant_memories=select_relevant_memories)

    await agent.build_system()
    await agent.inject_memory()

    prompt = agent.session.system_prompt
    assert selected_contexts[0][-1] == {"role": "user", "content": "review this"}
    assert "- **reviewing**: Review Python code" in prompt
    assert "<memory>" not in prompt
    assert agent.session.build_context()[-1] == {
        "role": "user",
        "content": (
            "Potentially relevant memory:\n"
            "<memory>\nPrefer focused tests.\n</memory>\n\n"
            "The current user request and conversation context take priority over "
            "recalled memory."
        ),
    }


@pytest.mark.asyncio
async def test_agent_preserves_explicit_system_prompt_without_memory_lookup() -> None:
    # An explicit system prompt bypasses dynamic skill and memory augmentation.
    agent = Agent(
        telemetry=AgentTelemetry(),
        system_prompt="Use the fixed system contract.",
    )
    agent.session = Session(
        sys_prompt="stale prompt",
        client=object(),  # type: ignore[arg-type]
    )

    async def unexpected_memory_lookup(messages: list[dict[str, Any]]) -> list[str]:
        pytest.fail(f"memory lookup was not expected: {messages}")

    agent.memory = SimpleNamespace(select_relevant_memories=unexpected_memory_lookup)

    await agent.build_system()

    assert agent.session.system_prompt == "Use the fixed system contract."


@pytest.mark.parametrize("finish_reason", ["stop", "length", "content_filter"])
def test_record_turn_completion_uses_provider_finish_reason(
    finish_reason: str,
) -> None:
    # Turn output and stop reason come directly from the final completion choice.
    agent = Agent(telemetry=AgentTelemetry())
    span = RecordingTurnSpan()
    completion = chat_completion(
        ChatCompletionMessage(role="assistant", content="final answer"),
        finish_reason=finish_reason,
    )

    agent._record_turn_completion(span, completion)

    assert span.output == "final answer"
    assert span.attributes == {"agent.stop_reason": finish_reason}


def test_record_turn_completion_uses_tool_call_message_as_output() -> None:
    # A tool-only terminal completion still produces a meaningful turn output.
    agent = Agent(telemetry=AgentTelemetry())
    span = RecordingTurnSpan()
    tool_call = ChatCompletionMessageFunctionToolCall(
        id="call-1",
        type="function",
        function=Function(name="echo", arguments="{}"),
    )
    completion = chat_completion(
        ChatCompletionMessage(role="assistant", content=None, tool_calls=[tool_call]),
        finish_reason="tool_calls",
    )

    agent._record_turn_completion(span, completion)

    assert span.output["tool_calls"][0]["id"] == "call-1"
    assert span.attributes == {"agent.stop_reason": "tool_calls"}


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
