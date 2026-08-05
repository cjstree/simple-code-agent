from contextlib import contextmanager
from typing import Any

from code_agent.telemetry import AgentTelemetry


class FakeSpan:
    def __init__(self) -> None:
        self.attributes: dict[str, Any] = {}

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value


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


def test_disabled_telemetry_is_a_noop() -> None:
    telemetry = AgentTelemetry.initialize(
        enabled=False,
        endpoint="http://localhost:6006/v1/traces",
        project_name="test",
    )

    with telemetry.trace_turn(session_id="session-1", prompt="hello") as span:
        span.set_output("world")
    telemetry.shutdown()
