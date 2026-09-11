"""Phoenix tracing setup and small agent-specific instrumentation helpers."""

import json
import logging
from contextlib import contextmanager, nullcontext
from pathlib import Path
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)

_SPAN_KIND = "openinference.span.kind"
_INPUT_VALUE = "input.value"
_INPUT_MIME_TYPE = "input.mime_type"
_OUTPUT_VALUE = "output.value"
_OUTPUT_MIME_TYPE = "output.mime_type"
_SESSION_ID = "session.id"
_TRACE_SCHEMA_VERSION = 1


class _SessionJsonlSpanExporter:
    """Write completed spans to one JSONL file per OpenInference session."""

    def __init__(self, directory: str | Path) -> None:
        self._directory = Path(directory)
        self._directory.mkdir(parents=True, exist_ok=True)
        self._manifest_path = self._directory / "manifest.jsonl"
        self._manifest_sessions: set[str] = set()
        self._lock = Lock()

    @staticmethod
    def _safe_session_id(session_id: Any) -> str:
        value = str(session_id) if session_id is not None else "unknown"
        safe_value = "".join(
            char if char.isalnum() or char in "-_." else "_" for char in value
        ).strip(".")
        return safe_value or "unknown"

    @staticmethod
    def _json_line(value: dict[str, Any]) -> str:
        return (
            json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            )
            + "\n"
        )

    def export(self, spans: Any) -> Any:
        """Append a compact OpenTelemetry JSON object for every completed span."""
        from opentelemetry.sdk.trace.export import SpanExportResult

        try:
            with self._lock:
                for span in spans:
                    attributes = span.attributes or {}
                    session_id = str(attributes.get(_SESSION_ID, "unknown"))
                    safe_session_id = self._safe_session_id(session_id)
                    filename = f"trace_log_{safe_session_id}.jsonl"

                    if session_id not in self._manifest_sessions:
                        manifest_record = {
                            "schema_version": _TRACE_SCHEMA_VERSION,
                            "session_id": session_id,
                            "file": filename,
                        }
                        with self._manifest_path.open("a", encoding="utf-8") as file:
                            file.write(self._json_line(manifest_record))
                        self._manifest_sessions.add(session_id)

                    span_record = json.loads(span.to_json(indent=None))
                    span_record = {
                        "schema_version": _TRACE_SCHEMA_VERSION,
                        **span_record,
                    }
                    output_path = self._directory / filename
                    with output_path.open("a", encoding="utf-8") as file:
                        file.write(self._json_line(span_record))
            return SpanExportResult.SUCCESS
        except Exception:
            logger.exception("Local trace export failed")
            return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        """No-op because files are opened and closed for each export batch."""

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        """Return immediately because writes are synchronous."""
        del timeout_millis
        return True


def _add_local_span_processor(
    tracer_provider: Any,
    trace_log_dir: str | Path,
    *,
    preserve_phoenix_processor: bool,
) -> None:
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    processor = SimpleSpanProcessor(_SessionJsonlSpanExporter(trace_log_dir))
    if preserve_phoenix_processor:
        tracer_provider.add_span_processor(
            processor,
            replace_default_processor=False,
        )
    else:
        tracer_provider.add_span_processor(processor)


class AgentSpan:
    """Expose only the span operations needed by the CLI agent."""

    def __init__(self, span: Any | None = None) -> None:
        self._span = span

    def set_output(self, output: Any | None) -> None:
        """Attach an unredacted textual result to the current span."""
        if self._span is None or output is None:
            return
        if isinstance(output, str):
            output_value = output
            output_mime_type = "text/plain"
        else:
            output_value = json.dumps(output, ensure_ascii=False, default=str)
            output_mime_type = "application/json"
        self._span.set_attribute(_OUTPUT_VALUE, output_value)
        self._span.set_attribute(_OUTPUT_MIME_TYPE, output_mime_type)

    def set_attribute(self, key: str, value: Any) -> None:
        """Attach an operation-specific attribute to the current span."""
        if self._span is not None:
            self._span.set_attribute(key, value)

    def record_exception(self, error: Exception) -> None:
        """Record a handled exception without changing application behavior."""
        if self._span is not None:
            self._span.record_exception(error)


class AgentTelemetry:
    """Own Phoenix registration and create OpenInference agent/tool spans."""

    def __init__(
        self,
        *,
        tracer: Any | None = None,
        tracer_provider: Any | None = None,
        openai_instrumentor: Any | None = None,
        using_session: Any | None = None,
    ) -> None:
        self._tracer = tracer
        self._tracer_provider = tracer_provider
        self._openai_instrumentor = openai_instrumentor
        self._using_session = using_session

    @classmethod
    def initialize(
        cls,
        *,
        enabled: bool,
        endpoint: str,
        project_name: str,
        trace_log_dir: str | Path | None = None,
    ) -> "AgentTelemetry":
        """Register remote and/or local tracing, failing open on errors."""
        if not enabled and trace_log_dir is None:
            logger.info("Tracing is disabled")
            return cls()

        tracer_provider = None
        phoenix_active = False
        try:
            # Telemetry dependencies are optional and only needed when enabled.
            from openinference.instrumentation import TracerProvider, using_session
            from openinference.instrumentation.openai import OpenAIInstrumentor

            if enabled:
                try:
                    from phoenix.otel import register

                    tracer_provider = register(
                        endpoint=endpoint,
                        project_name=project_name,
                        protocol="http/protobuf",
                        batch=True,
                    )
                    phoenix_active = True
                    logger.info(
                        "Phoenix tracing initialized project=%s endpoint=%s",
                        project_name,
                        endpoint,
                    )
                except Exception:
                    logger.exception("Phoenix tracing initialization failed")

            if tracer_provider is None and trace_log_dir is not None:
                from opentelemetry.sdk.resources import Resource

                tracer_provider = TracerProvider(
                    resource=Resource.create(
                        {"openinference.project.name": project_name}
                    )
                )

            if tracer_provider is None:
                return cls()

            if trace_log_dir is not None:
                try:
                    _add_local_span_processor(
                        tracer_provider,
                        trace_log_dir,
                        preserve_phoenix_processor=phoenix_active,
                    )
                    logger.info("Local trace export initialized dir=%s", trace_log_dir)
                except Exception:
                    logger.exception("Local trace export initialization failed")
                    if not phoenix_active:
                        tracer_provider.shutdown()
                        return cls()

            openai_instrumentor = OpenAIInstrumentor()
            openai_instrumentor.instrument(tracer_provider=tracer_provider)
            return cls(
                tracer=tracer_provider.get_tracer("code_agent"),
                tracer_provider=tracer_provider,
                openai_instrumentor=openai_instrumentor,
                using_session=using_session,
            )
        except Exception:
            logger.exception("Tracing initialization failed; continuing disabled")
            if tracer_provider is not None:
                try:
                    tracer_provider.shutdown()
                except Exception:
                    logger.exception("Tracing cleanup after initialization failed")
            return cls()

    @contextmanager
    def trace_turn(self, *, session_id: str, prompt: str):
        """Trace one user turn and propagate its session to all child spans."""
        if self._tracer is None:
            yield AgentSpan()
            return

        session_context = (
            self._using_session(session_id)
            if self._using_session is not None
            else nullcontext()
        )
        attributes = {
            _SPAN_KIND: "agent",
            _SESSION_ID: session_id,
            _INPUT_VALUE: prompt,
            _INPUT_MIME_TYPE: "text/plain",
        }
        with (
            session_context,
            self._tracer.start_as_current_span(
                "agent.turn", attributes=attributes
            ) as span,
        ):
            yield AgentSpan(span)

    @contextmanager
    def trace_tool(
        self,
        *,
        tool_call_id: str,
        tool_name: str,
        arguments: str | dict[str, Any],
    ):
        """Trace a local or MCP tool invocation with complete input and output."""
        if self._tracer is None:
            yield AgentSpan()
            return

        input_value = (
            arguments
            if isinstance(arguments, str)
            else json.dumps(arguments, ensure_ascii=False, default=str)
        )
        attributes = {
            _SPAN_KIND: "tool",
            "tool.name": tool_name,
            "tool.call.id": tool_call_id,
            _INPUT_VALUE: input_value,
            _INPUT_MIME_TYPE: "application/json",
        }
        with self._tracer.start_as_current_span(
            f"tool.{tool_name}", attributes=attributes
        ) as span:
            yield AgentSpan(span)

    @contextmanager
    def trace_operation(
        self,
        *,
        name: str,
        span_kind: str,
        input_value: Any,
    ):
        """Trace one agent subsystem operation as a child of the current span."""
        if self._tracer is None:
            yield AgentSpan()
            return

        if isinstance(input_value, str):
            serialized_input = input_value
            input_mime_type = "text/plain"
        else:
            serialized_input = json.dumps(
                input_value, ensure_ascii=False, default=str
            )
            input_mime_type = "application/json"
        attributes = {
            _SPAN_KIND: span_kind,
            _INPUT_VALUE: serialized_input,
            _INPUT_MIME_TYPE: input_mime_type,
        }
        with self._tracer.start_as_current_span(
            name, attributes=attributes
        ) as span:
            yield AgentSpan(span)

    def shutdown(self) -> None:
        """Flush queued spans before the CLI process exits."""
        if self._openai_instrumentor is not None:
            try:
                self._openai_instrumentor.uninstrument()
            except Exception:
                logger.exception("OpenAI tracing shutdown failed")
        if self._tracer_provider is not None:
            try:
                self._tracer_provider.shutdown()
            except Exception:
                logger.exception("Trace flush failed")
