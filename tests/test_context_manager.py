from contextlib import contextmanager
from typing import Any

import pytest

from code_agent.context_manager import ContextManager, tool_call_range


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


def tool_result(tool_call_id: str, content: str) -> dict[str, str]:
    return {"role": "tool", "tool_call_id": tool_call_id, "content": content}


def assistant_tool_calls(*tool_call_ids: str) -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": tool_call_id,
                "type": "function",
                "function": {"name": "demo", "arguments": "{}"},
            }
            for tool_call_id in tool_call_ids
        ],
    }


def assert_tool_call_groups_are_atomic(messages: list[dict]) -> None:
    """Assert that no retained assistant call or tool result is left dangling."""
    active_ids: list[str] | None = None
    returned_ids: list[str] = []

    for message in [*messages, {"role": "end"}]:
        if message.get("role") == "tool":
            assert active_ids is not None, "tool result has no preceding tool call"
            returned_ids.append(message["tool_call_id"])
            continue

        if active_ids is not None:
            assert returned_ids == active_ids
            active_ids = None
            returned_ids = []

        if message.get("role") == "assistant" and message.get("tool_calls"):
            active_ids = [call["id"] for call in message["tool_calls"]]


def test_tool_call_range_finds_assistant_and_all_contiguous_results() -> None:
    messages = [
        {"role": "user", "content": "run"},
        assistant_tool_calls("one", "two"),
        tool_result("one", "first"),
        tool_result("two", "second"),
        {"role": "assistant", "content": "done"},
    ]

    assert tool_call_range(messages, 2) == (1, 4)


def test_snip_compact_keeps_head_tool_call_group_atomic() -> None:
    manager = ContextManager(object())
    manager.max_messages = 8
    messages = [
        {"role": "user", "content": "first"},
        assistant_tool_calls("head-a", "head-b"),
        tool_result("head-a", "a"),
        tool_result("head-b", "b"),
        assistant_tool_calls("middle"),
        tool_result("middle", "middle"),
        {"role": "assistant", "content": "done"},
        {"role": "user", "content": "second"},
        assistant_tool_calls("tail-a", "tail-b"),
        tool_result("tail-a", "a"),
        tool_result("tail-b", "b"),
        {"role": "assistant", "content": "done again"},
    ]

    compacted = manager._snip_compact(messages)
    
    assert_tool_call_groups_are_atomic(compacted)


def test_snip_compact_keeps_tail_tool_call_group_atomic() -> None:
    manager = ContextManager(object())
    manager.max_messages = 6
    messages = [
        {"role": "user", "content": "one"},
        {"role": "assistant", "content": "one done"},
        {"role": "user", "content": "two"},
        {"role": "assistant", "content": "two done"},
        {"role": "user", "content": "three"},
        {"role": "assistant", "content": "three done"},
        {"role": "user", "content": "four"},
        {"role": "assistant", "content": "four done"},
        {"role": "user", "content": "use tools"},
        assistant_tool_calls("tail-a", "tail-b"),
        tool_result("tail-a", "a"),
        tool_result("tail-b", "b"),
        {"role": "assistant", "content": "all done"},
    ]

    compacted = manager._snip_compact(messages)
    assert_tool_call_groups_are_atomic(compacted)


def test_micro_compact_keeps_recent_results_and_allows_old_small_results() -> None:
    manager = ContextManager(object())
    manager.max_tool_res = 2
    old_large = "a" * 121
    old_small = "small"
    recent_one = "b" * 121
    recent_two = "c" * 121
    messages = [
        {"role": "user", "content": "run"},
        assistant_tool_calls("old-large"),
        tool_result("old-large", old_large),
        assistant_tool_calls("old-small"),
        tool_result("old-small", old_small),
        assistant_tool_calls("recent-one"),
        tool_result("recent-one", recent_one),
        assistant_tool_calls("recent-two"),
        tool_result("recent-two", recent_two),
        {"role": "assistant", "content": "done"},
    ]

    messages = manager._micro_compact(messages)

    results = {
        message["tool_call_id"]: message["content"]
        for message in messages
        if message["role"] == "tool"
    }
    assert results["old-large"].startswith("<tool-result-truncated>")
    assert results["old-small"] == old_small
    assert results["recent-one"] == recent_one
    assert results["recent-two"] == recent_two


@pytest.mark.asyncio
async def test_compact_uses_summary_after_context_limit_is_exceeded(
    monkeypatch,
) -> None:
    telemetry = RecordingTelemetry()
    manager = ContextManager(
        object(), telemetry=telemetry  # type: ignore[arg-type]
    )
    manager.context_limit = 0
    manager.max_messages = 100
    messages = [
        {"role": "user", "content": "original goal"},
        {"role": "assistant", "content": "work so far"},
    ]

    async def summarize(value: list[dict[str, str]]) -> str:
        return "summary"

    monkeypatch.setattr(manager, "_summary_history", summarize)

    compacted = await manager.compact(messages)

    assert compacted == [
        messages[0],
        {"role": "user", "content": "[Compacted]\n\nsummary"},
    ]
    name, span_kind, input_value, span = telemetry.operations[0]
    assert name == "context.compact"
    assert span_kind == "chain"
    assert input_value == messages
    assert span.attributes["context.summarized"] is True
    assert span.attributes["context.input.message_count"] == 2
    assert span.attributes["context.output.message_count"] == 2
    assert span.output == compacted


def test_tool_res_compact_persists_largest_current_round_results(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    telemetry = RecordingTelemetry()
    manager = ContextManager(
        object(),
        persist_preview_chars=4,
        telemetry=telemetry,  # type: ignore[arg-type]
    )
    manager.max_tool_round_res = 200
    manager.persist_threshold = 100
    large_content = "你好世界" * 50
    messages = [
        tool_result("old", "x" * 20),
        {"role": "assistant", "content": "next round"},
        tool_result("small", "a" * 50),
        tool_result("large", large_content),
    ]

    result = manager._tool_res_compact(messages)

    assert result is messages
    assert messages[0]["content"] == "x" * 20
    assert messages[2]["content"] == "a" * 50
    assert messages[3]["content"].startswith("<tool-result-persisted>\n你好世界...")
    assert (
        tmp_path / "task_output/tool_results/large.txt"
    ).read_text() == large_content
    name, span_kind, _, span = telemetry.operations[0]
    assert name == "context.compact_tool_results"
    assert span_kind == "chain"
    assert span.attributes["context.tool_result.count"] == 2
    assert span.attributes["context.tool_result.persisted_count"] == 1
    assert span.output is messages


def test_tool_res_compact_stops_when_remaining_results_are_below_threshold(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    manager = ContextManager(object())
    manager.max_tool_round_res = 5
    manager.persist_threshold = 4
    messages = [tool_result("one", "abc"), tool_result("two", "def")]

    manager._tool_res_compact(messages)

    assert [message["content"] for message in messages] == ["abc", "def"]
    assert not (tmp_path / "task_output").exists()


def test_tool_res_compact_counts_persisted_placeholder_bytes(monkeypatch) -> None:
    manager = ContextManager(object())
    manager.max_tool_round_res = 11
    manager.persist_threshold = 1
    messages = [tool_result("large", "a" * 10), tool_result("small", "b" * 8)]
    monkeypatch.setattr(manager, "_persist_large_output", lambda message: "done")

    manager._tool_res_compact(messages)

    assert [message["content"] for message in messages] == ["done", "done"]
