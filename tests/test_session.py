from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from openai.types import CompletionUsage

from code_agent.session import Session


class RecordingSpan:
    def __init__(self) -> None:
        self.attributes: dict[str, Any] = {}
        self.output: Any = None

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def set_output(self, output: Any) -> None:
        self.output = output


class RecordingTelemetry:
    def __init__(self) -> None:
        self.operations: list[tuple[str, str, Any, RecordingSpan]] = []

    @contextmanager
    def trace_operation(self, *, name: str, span_kind: str, input_value: Any):
        span = RecordingSpan()
        self.operations.append((name, span_kind, input_value, span))
        yield span


class FakeCompletions:
    async def create(self, **request: Any) -> Any:
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="summary"))],
            usage=CompletionUsage(
                completion_tokens=2,
                prompt_tokens=8,
                total_tokens=10,
            ),
        )


def make_session(telemetry: RecordingTelemetry, **kwargs: Any) -> Session:
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions())
    )
    return Session(
        sys_prompt="system",
        client=client,  # type: ignore[arg-type]
        telemetry=telemetry,  # type: ignore[arg-type]
        reserved_token=0,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_compact_records_unchanged_active_context() -> None:
    # A no-op compact reports that its active context did not change.
    telemetry = RecordingTelemetry()
    session = make_session(telemetry, thresh_hold=100_000)
    session.append_message({"role": "user", "content": "hello"})

    await session.compact()

    name, span_kind, input_value, span = telemetry.operations[0]
    assert name == "session.compact"
    assert span_kind == "chain"
    assert input_value == {"active_message_count": 2}
    assert span.output == {"active_context_changed": False}


@pytest.mark.asyncio
async def test_compact_records_changed_active_context() -> None:
    # A changed active context is traced as its complete before-and-after values.
    telemetry = RecordingTelemetry()
    session = make_session(telemetry, thresh_hold=100_000)
    session.max_tool_res = 1
    for tool_call_id, content in (
        ("old-one", "a" * 300),
        ("old-two", "b" * 300),
        ("recent", "c" * 300),
    ):
        session.append_message(
            {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": content,
            }
        )

    context_before = session.build_context()
    await session.compact()

    name, span_kind, input_value, span = telemetry.operations[0]
    assert name == "session.compact"
    assert span_kind == "chain"
    assert input_value == {"active_message_count": 4}
    assert span.output == {
        "active_context_changed": True,
        "before": context_before,
        "after": session.build_context(),
    }
    assert span.output["before"][1]["content"] == "a" * 300
    assert span.output["after"][1]["content"].startswith(
        "<tool-result-truncated>"
    )


@pytest.mark.asyncio
async def test_compact_history_is_traced_only_when_triggered() -> None:
    # History tracing exists only when summarization actually runs.
    telemetry = RecordingTelemetry()
    session = make_session(telemetry, thresh_hold=1)
    session.append_message({"role": "user", "content": "original goal"})
    token_before = session.context_token

    await session.compact()

    assert [operation[0] for operation in telemetry.operations] == [
        "session.compact",
        "session.compact_history",
    ]
    _, span_kind, input_value, history_span = telemetry.operations[1]
    assert span_kind == "chain"
    assert input_value == {"context_token": token_before}
    assert history_span.output == "summary"
    assert telemetry.operations[0][3].output["active_context_changed"] is True


@pytest.mark.asyncio
async def test_compact_history_returns_whether_an_entry_was_added() -> None:
    # The return value is true only when history summarization adds a compact entry.
    session = make_session(RecordingTelemetry(), thresh_hold=100_000)
    session.append_message({"role": "user", "content": "original goal"})

    assert await session.compact_history() is False

    session.compact_thresh_hold = 1

    assert await session.compact_history() is True


def test_micro_compact_returns_whether_an_entry_changed() -> None:
    # The return value changes only when a tool entry receives replacement content.
    session = make_session(RecordingTelemetry(), thresh_hold=100_000)
    session.max_tool_res = 1
    session.append_message(
        {"role": "tool", "tool_call_id": "recent", "content": "a" * 300}
    )

    assert session._micro_compact() is False

    session.append_message(
        {"role": "tool", "tool_call_id": "new", "content": "b" * 300}
    )

    assert session._micro_compact() is True
    assert session._micro_compact() is False


def test_tool_result_compact_returns_whether_an_entry_changed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The return value reports whether an oversized tool result was persisted.
    session = make_session(RecordingTelemetry())
    session.max_tool_round_res = 20
    session.persist_threshold = 1
    session.append_message(
        {"role": "tool", "tool_call_id": "large", "content": "a" * 10}
    )

    assert session.tool_res_compact() is False

    session.max_tool_round_res = 5
    monkeypatch.setattr(
        session,
        "_persist_large_output",
        lambda entry, content: content,
    )

    assert session.tool_res_compact() is False

    monkeypatch.setattr(
        session,
        "_persist_large_output",
        lambda entry, content: "done",
    )

    assert session.tool_res_compact() is True
