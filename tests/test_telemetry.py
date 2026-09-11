import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from code_agent.telemetry import AgentTelemetry, _add_local_span_processor


class FakeSpan:
    def __init__(self) -> None:
        self.attributes: dict[str, Any] = {}
        self.exceptions: list[Exception] = []

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def record_exception(self, error: Exception) -> None:
        self.exceptions.append(error)


class FakeTracer:
    def __init__(self) -> None:
        self.started: list[tuple[str, dict[str, Any], FakeSpan]] = []

    @contextmanager
    def start_as_current_span(self, name: str, *, attributes: dict[str, Any]):
        span = FakeSpan()
        self.started.append((name, attributes, span))
        yield span


class FakeSessionContext:
    def __init__(self) -> None:
        self.session_ids: list[str] = []

    @contextmanager
    def __call__(self, session_id: str):
        self.session_ids.append(session_id)
        yield


def test_turn_span_records_session_unredacted_input_and_output() -> None:
    tracer = FakeTracer()
    sessions = FakeSessionContext()
    telemetry = AgentTelemetry(tracer=tracer, using_session=sessions)

    with telemetry.trace_turn(session_id="session-1", prompt="完整问题") as span:
        span.set_output("完整回答")

    name, attributes, recorded_span = tracer.started[0]
    assert name == "agent.turn"
    assert sessions.session_ids == ["session-1"]
    assert attributes == {
        "openinference.span.kind": "agent",
        "session.id": "session-1",
        "input.value": "完整问题",
        "input.mime_type": "text/plain",
    }
    assert recorded_span.attributes == {
        "output.value": "完整回答",
        "output.mime_type": "text/plain",
    }


def test_tool_span_records_raw_arguments_and_result() -> None:
    tracer = FakeTracer()
    telemetry = AgentTelemetry(tracer=tracer)

    with telemetry.trace_tool(
        tool_call_id="call-1",
        tool_name="search_knowledge",
        arguments='{"query":"原始问题"}',
    ) as span:
        span.set_output('{"matches":["完整结果"]}')

    name, attributes, recorded_span = tracer.started[0]
    assert name == "tool.search_knowledge"
    assert attributes["openinference.span.kind"] == "tool"
    assert attributes["tool.name"] == "search_knowledge"
    assert attributes["tool.call.id"] == "call-1"
    assert attributes["input.value"] == '{"query":"原始问题"}'
    assert recorded_span.attributes["output.value"] == '{"matches":["完整结果"]}'


def test_operation_span_records_structured_input_output_and_attributes() -> None:
    tracer = FakeTracer()
    telemetry = AgentTelemetry(tracer=tracer)
    error = ValueError("invalid response")

    with telemetry.trace_operation(
        name="memory.select_relevant",
        span_kind="retriever",
        input_value={"query": "完整问题"},
    ) as span:
        span.set_attribute("memory.selected.count", 1)
        span.record_exception(error)
        span.set_output(["完整记忆"])

    name, attributes, recorded_span = tracer.started[0]
    assert name == "memory.select_relevant"
    assert attributes == {
        "openinference.span.kind": "retriever",
        "input.value": '{"query": "完整问题"}',
        "input.mime_type": "application/json",
    }
    assert recorded_span.attributes == {
        "memory.selected.count": 1,
        "output.value": '["完整记忆"]',
        "output.mime_type": "application/json",
    }
    assert recorded_span.exceptions == [error]


def test_disabled_telemetry_is_a_noop() -> None:
    telemetry = AgentTelemetry.initialize(
        enabled=False,
        endpoint="http://localhost:6006/v1/traces",
        project_name="test",
    )

    with telemetry.trace_turn(session_id="session-1", prompt="hello") as span:
        span.set_output("world")
    telemetry.shutdown()


def test_local_trace_export_writes_session_jsonl_and_manifest(
    tmp_path: Path,
) -> None:
    # Local-only tracing preserves span relationships and routes a session to one file.
    pytest.importorskip("opentelemetry.sdk")
    pytest.importorskip("openinference.instrumentation")
    pytest.importorskip("openinference.instrumentation.openai")
    telemetry = AgentTelemetry.initialize(
        enabled=False,
        endpoint="http://localhost:6006/v1/traces",
        project_name="test-project",
        trace_log_dir=tmp_path,
    )

    with (
        telemetry.trace_turn(session_id="session-1", prompt="完整问题"),
        telemetry.trace_operation(
            name="session.compact_history",
            span_kind="chain",
            input_value={"context_token": 100},
        ) as span,
    ):
        span.set_output("完整摘要")
    telemetry.shutdown()

    trace_path = tmp_path / "trace_log_session-1.jsonl"
    records = [json.loads(line) for line in trace_path.read_text().splitlines()]
    assert [record["name"] for record in records] == [
        "session.compact_history",
        "agent.turn",
    ]
    assert all(record["schema_version"] == 1 for record in records)
    assert all(record["attributes"]["session.id"] == "session-1" for record in records)
    assert records[0]["parent_id"] == records[1]["context"]["span_id"]
    assert records[0]["resource"]["attributes"]["openinference.project.name"] == (
        "test-project"
    )

    manifest = [
        json.loads(line)
        for line in (tmp_path / "manifest.jsonl").read_text().splitlines()
    ]
    assert manifest == [
        {
            "schema_version": 1,
            "session_id": "session-1",
            "file": "trace_log_session-1.jsonl",
        }
    ]


def test_local_processor_preserves_the_phoenix_processor(tmp_path: Path) -> None:
    # Adding local export must not replace Phoenix's default OTLP processor.
    pytest.importorskip("opentelemetry.sdk")

    class FakeProvider:
        def __init__(self) -> None:
            self.calls: list[tuple[Any, dict[str, Any]]] = []

        def add_span_processor(self, processor: Any, **kwargs: Any) -> None:
            self.calls.append((processor, kwargs))

    provider = FakeProvider()
    _add_local_span_processor(
        provider,
        tmp_path,
        preserve_phoenix_processor=True,
    )

    assert len(provider.calls) == 1
    assert provider.calls[0][1] == {"replace_default_processor": False}


def test_invalid_local_trace_directory_fails_open(tmp_path: Path) -> None:
    # An unusable local destination leaves agent telemetry as a safe no-op.
    pytest.importorskip("opentelemetry.sdk")
    pytest.importorskip("openinference.instrumentation")
    pytest.importorskip("openinference.instrumentation.openai")
    not_a_directory = tmp_path / "trace-file"
    not_a_directory.write_text("occupied", encoding="utf-8")

    telemetry = AgentTelemetry.initialize(
        enabled=False,
        endpoint="http://localhost:6006/v1/traces",
        project_name="test-project",
        trace_log_dir=not_a_directory,
    )

    with telemetry.trace_turn(session_id="session-1", prompt="hello") as span:
        span.set_output("world")
    telemetry.shutdown()
