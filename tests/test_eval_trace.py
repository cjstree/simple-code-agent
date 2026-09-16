import json

import pytest

from code_agent_evals.trace import (
    TraceFormatError,
    TraceUnavailableError,
    parse_trace_jsonl,
    read_trace_input,
)


def _span(
    name: str,
    span_id: str,
    *,
    start_second: int,
    parent_id: str | None = None,
    kind: str = "chain",
    output: str | None = None,
    session_id: str = "session-1",
    trace_id: str = "trace-1",
) -> dict:
    attributes = {
        "session.id": session_id,
        "openinference.span.kind": kind,
    }
    if output is not None:
        attributes["output.value"] = output
    return {
        "schema_version": 1,
        "name": name,
        "context": {"trace_id": trace_id, "span_id": span_id},
        "parent_id": parent_id,
        "start_time": f"2026-09-14T00:00:{start_second:02d}.000000Z",
        "end_time": f"2026-09-14T00:00:{start_second:02d}.500000Z",
        "attributes": attributes,
    }


def _jsonl(*records: dict) -> str:
    return "".join(json.dumps(record) + "\n" for record in records)


def _manifest(filename: str = "trace_log_session-1.jsonl") -> str:
    return _jsonl(
        {"schema_version": 1, "session_id": "session-1", "file": filename}
    )


def test_trace_parser_reconstructs_turns_from_completion_order() -> None:
    # Child-first JSONL is ordered by timestamps and bound through parent ancestry.
    content = _jsonl(
        _span(
            "tool.read_file",
            "tool-1",
            start_second=4,
            parent_id="llm-1",
            kind="tool",
            output="file contents",
        ),
        _span(
            "session.compact_history",
            "compact-1",
            start_second=3,
            parent_id="turn-2",
            output="summary MARKER-7",
        ),
        _span("agent.turn", "turn-2", start_second=2, kind="agent"),
        _span("llm.chat", "llm-1", start_second=3, parent_id="turn-2", kind="llm"),
        _span("agent.turn", "turn-1", start_second=1, kind="agent"),
    )

    trace = parse_trace_jsonl(
        _manifest(), {"trace_log_session-1.jsonl": content}
    )
    session = trace.session("session-1")

    assert [turn.span.span_id for turn in session.turns] == ["turn-1", "turn-2"]
    assert [span.span_id for span in session.turn(2).descendants] == [
        "compact-1",
        "llm-1",
        "tool-1",
    ]
    assert session.turn(2).compact_history_spans[0].output_value == (
        "summary MARKER-7"
    )
    assert session.turn(2).tool_spans[0].span_id == "tool-1"
    assert len(session.compact_history_between(1, 2)) == 1


def test_evaluation_trace_orders_turns_across_restarted_sessions() -> None:
    # Global turn windows remain usable when a fresh Agent creates a new session.
    manifest = _jsonl(
        {
            "schema_version": 1,
            "session_id": "session-1",
            "file": "first.jsonl",
        },
        {
            "schema_version": 1,
            "session_id": "session-2",
            "file": "second.jsonl",
        },
    )
    files = {
        "first.jsonl": _jsonl(
            _span("agent.turn", "turn-1", start_second=1, kind="agent")
        ),
        "second.jsonl": _jsonl(
            _span(
                "session.compact_history",
                "compact-2",
                start_second=4,
                parent_id="turn-2",
                session_id="session-2",
                trace_id="trace-2",
            ),
            _span(
                "agent.turn",
                "turn-2",
                start_second=3,
                kind="agent",
                session_id="session-2",
                trace_id="trace-2",
            ),
        ),
    }

    trace = parse_trace_jsonl(manifest, files)

    assert [turn.span.session_id for turn in trace.turns] == [
        "session-1",
        "session-2",
    ]
    assert trace.turn(2).span.span_id == "turn-2"
    assert len(trace.compact_history_between(1, 2)) == 1


@pytest.mark.parametrize(
    ("manifest", "files", "message"),
    [
        ("not-json\n", {}, "invalid JSON"),
        (_manifest("../trace.jsonl"), {}, "inside the trace directory"),
        (_manifest(), {}, "missing trace file"),
        (
            _manifest(),
            {"trace_log_session-1.jsonl": "{broken\n"},
            "invalid JSON",
        ),
    ],
)
def test_trace_parser_rejects_missing_or_damaged_jsonl(
    manifest: str, files: dict[str, str], message: str
) -> None:
    # Missing, malformed, or path-escaping trace inputs are infrastructure errors.
    with pytest.raises((TraceFormatError, TraceUnavailableError), match=message):
        parse_trace_jsonl(manifest, files)


def test_trace_parser_rejects_unbound_scoring_span() -> None:
    # A compact span with no agent.turn ancestor cannot be credited to a turn.
    content = _jsonl(
        _span("agent.turn", "turn-1", start_second=1, kind="agent"),
        _span(
            "session.compact_history",
            "compact-1",
            start_second=2,
            parent_id="missing-parent",
        ),
    )

    with pytest.raises(TraceFormatError, match="not bound to an agent.turn"):
        parse_trace_jsonl(
            _manifest(), {"trace_log_session-1.jsonl": content}
        )


@pytest.mark.asyncio
async def test_trace_reader_loads_manifest_references_from_fixed_directory() -> None:
    # The sandbox adapter reads only the fixed manifest and its declared trace file.
    content = _jsonl(_span("agent.turn", "turn-1", start_second=1, kind="agent"))

    class FakeSandbox:
        def __init__(self) -> None:
            self.paths: list[str] = []

        async def read_file(self, path: str) -> str:
            self.paths.append(path)
            if path == ".eval_traces/manifest.jsonl":
                return _manifest()
            if path == ".eval_traces/trace_log_session-1.jsonl":
                return content
            raise FileNotFoundError(path)

    environment = FakeSandbox()

    trace = await read_trace_input(environment)

    assert len(trace.session("session-1").turns) == 1
    assert environment.paths == [
        ".eval_traces/manifest.jsonl",
        ".eval_traces/trace_log_session-1.jsonl",
    ]


@pytest.mark.asyncio
async def test_trace_reader_classifies_missing_manifest_as_unavailable() -> None:
    # A sandbox without exported trace data is distinct from an ordinary score zero.
    class EmptySandbox:
        async def read_file(self, path: str) -> str:
            raise FileNotFoundError(path)

    with pytest.raises(TraceUnavailableError, match="manifest is unavailable"):
        await read_trace_input(EmptySandbox())
