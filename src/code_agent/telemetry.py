"""Phoenix tracing setup and small agent-specific instrumentation helpers."""

import json
import logging
from contextlib import contextmanager, nullcontext
from typing import Any

logger = logging.getLogger(__name__)

_SPAN_KIND = "openinference.span.kind"
_INPUT_VALUE = "input.value"
_INPUT_MIME_TYPE = "input.mime_type"
_OUTPUT_VALUE = "output.value"
_OUTPUT_MIME_TYPE = "output.mime_type"
_SESSION_ID = "session.id"


class AgentSpan:
    """Expose only the span operations needed by the CLI agent."""

    def __init__(self, span: Any | None = None) -> None:
        self._span = span

    def set_output(self, output: str | None) -> None:
        """Attach an unredacted textual result to the current span."""
        if self._span is None or output is None:
            return
        self._span.set_attribute(_OUTPUT_VALUE, output)
        self._span.set_attribute(_OUTPUT_MIME_TYPE, "text/plain")


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
    ) -> "AgentTelemetry":
        """Register Phoenix and OpenAI instrumentation, failing open on errors."""
        if not enabled:
            logger.info("Phoenix tracing is disabled")
            return cls()

        try:
            from openinference.instrumentation.openai import OpenAIInstrumentor
            from phoenix.otel import register, using_session

            tracer_provider = register(
                endpoint=endpoint,
                project_name=project_name,
                protocol="http/protobuf",
                batch=True,
            )
            openai_instrumentor = OpenAIInstrumentor()
            openai_instrumentor.instrument(tracer_provider=tracer_provider)
            logger.info(
                "Phoenix tracing initialized project=%s endpoint=%s",
                project_name,
                endpoint,
            )
            return cls(
                tracer=tracer_provider.get_tracer("code_agent"),
                tracer_provider=tracer_provider,
                openai_instrumentor=openai_instrumentor,
                using_session=using_session,
            )
        except Exception:
            logger.exception(
                "Phoenix tracing initialization failed; continuing disabled"
            )
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
                logger.exception("Phoenix trace flush failed")
