"""Parse local JSONL traces into scorer-facing turn records."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Any, Protocol

TRACE_DIRECTORY = PurePosixPath(".eval_traces")
TRACE_MANIFEST = "manifest.jsonl"
TRACE_SCHEMA_VERSION = 1
_SCORING_SPAN_NAMES = {"session.compact", "session.compact_history"}


class TraceInputError(RuntimeError):
    """Base class for unusable trace input (an infrastructure failure)."""


class TraceUnavailableError(TraceInputError):
    """The expected trace manifest or a referenced trace file is missing."""


class TraceFormatError(TraceInputError):
    """Trace JSONL exists but does not satisfy the eval input contract."""


class TraceSandbox(Protocol):
    """The subset of Inspect's sandbox API needed to load trace input."""

    async def read_file(self, path: str) -> str: ...


@dataclass(frozen=True)
class TraceSpan:
    """A validated completed span from the local telemetry exporter."""

    session_id: str
    name: str
    trace_id: str
    span_id: str
    parent_id: str | None
    start_time: datetime
    end_time: datetime
    attributes: Mapping[str, Any]
    raw: Mapping[str, Any]

    @property
    def is_tool(self) -> bool:
        """Whether OpenInference classifies this span as a tool call."""
        return self.attributes.get("openinference.span.kind") == "tool"

    @property
    def output_value(self) -> Any | None:
        """Return the unredacted output recorded by AgentTelemetry, if present."""
        return self.attributes.get("output.value")


@dataclass(frozen=True)
class TurnTrace:
    """One agent turn and all of its descendant spans in execution order."""

    ordinal: int
    span: TraceSpan
    descendants: tuple[TraceSpan, ...]

    def spans_named(self, name: str) -> tuple[TraceSpan, ...]:
        """Return descendant spans with an exact operation name."""
        return tuple(span for span in self.descendants if span.name == name)

    @property
    def compact_spans(self) -> tuple[TraceSpan, ...]:
        return self.spans_named("session.compact")

    @property
    def compact_history_spans(self) -> tuple[TraceSpan, ...]:
        return self.spans_named("session.compact_history")

    @property
    def tool_spans(self) -> tuple[TraceSpan, ...]:
        return tuple(span for span in self.descendants if span.is_tool)


@dataclass(frozen=True)
class SessionTrace:
    """All parsed evidence for one exporter session."""

    session_id: str
    source_file: str
    spans: tuple[TraceSpan, ...]
    turns: tuple[TurnTrace, ...]

    def turn(self, ordinal: int) -> TurnTrace:
        """Look up a one-based turn ordinal."""
        if ordinal < 1 or ordinal > len(self.turns):
            raise IndexError(f"session {self.session_id!r} has no turn {ordinal}")
        return self.turns[ordinal - 1]

    def compact_history_between(
        self, introduced_turn: int, used_turn: int
    ) -> tuple[TraceSpan, ...]:
        """Return history compactions after introduction and through use."""
        if introduced_turn < 1 or used_turn <= introduced_turn:
            raise ValueError(
                "turn window must satisfy 1 <= introduced_turn < used_turn"
            )
        self.turn(introduced_turn)
        self.turn(used_turn)
        return tuple(
            span
            for turn in self.turns[introduced_turn:used_turn]
            for span in turn.compact_history_spans
        )


@dataclass(frozen=True)
class EvaluationTrace:
    """Scorer input containing every session referenced by the manifest."""

    sessions: Mapping[str, SessionTrace]

    @property
    def turns(self) -> tuple[TurnTrace, ...]:
        """Return turns across all sessions in timestamp order."""
        return tuple(
            sorted(
                (turn for session in self.sessions.values() for turn in session.turns),
                key=lambda turn: _span_sort_key(turn.span),
            )
        )

    def turn(self, ordinal: int) -> TurnTrace:
        """Look up a one-based turn ordinal across the complete evaluation."""
        turns = self.turns
        if ordinal < 1 or ordinal > len(turns):
            raise IndexError(f"evaluation trace has no turn {ordinal}")
        return turns[ordinal - 1]

    def compact_history_between(
        self, introduced_turn: int, used_turn: int
    ) -> tuple[TraceSpan, ...]:
        """Return history compactions in a global, possibly cross-session window."""
        if introduced_turn < 1 or used_turn <= introduced_turn:
            raise ValueError(
                "turn window must satisfy 1 <= introduced_turn < used_turn"
            )
        self.turn(introduced_turn)
        self.turn(used_turn)
        return tuple(
            span
            for turn in self.turns[introduced_turn:used_turn]
            for span in turn.compact_history_spans
        )

    def session(self, session_id: str) -> SessionTrace:
        try:
            return self.sessions[session_id]
        except KeyError as error:
            raise KeyError(f"trace has no session {session_id!r}") from error


def _parse_jsonl(text: str, source: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise TraceFormatError(
                f"{source}:{line_number}: invalid JSON: {error.msg}"
            ) from error
        if not isinstance(record, dict):
            raise TraceFormatError(f"{source}:{line_number}: record must be an object")
        records.append(record)
    if not records:
        raise TraceFormatError(f"{source}: JSONL input is empty")
    return records


def _required_string(record: Mapping[str, Any], key: str, source: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value:
        raise TraceFormatError(f"{source}: {key} must be a non-empty string")
    return value


def _timestamp(record: Mapping[str, Any], key: str, source: str) -> datetime:
    value = _required_string(record, key, source)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise TraceFormatError(
            f"{source}: {key} is not an ISO-8601 timestamp"
        ) from error
    if parsed.tzinfo is None:
        raise TraceFormatError(f"{source}: {key} must include a timezone")
    return parsed


def _manifest_filename(value: Any, source: str) -> str:
    if not isinstance(value, str) or not value:
        raise TraceFormatError(f"{source}: file must be a non-empty string")
    path = PurePosixPath(value)
    if path.is_absolute() or len(path.parts) != 1 or path.name in {".", ".."}:
        raise TraceFormatError(f"{source}: file must stay inside the trace directory")
    return path.name


def _parse_span(
    record: dict[str, Any],
    *,
    expected_session_id: str,
    source: str,
    allow_unscoped: bool = False,
) -> TraceSpan:
    if record.get("schema_version") != TRACE_SCHEMA_VERSION:
        raise TraceFormatError(
            f"{source}: unsupported schema_version {record.get('schema_version')!r}"
        )
    name = _required_string(record, "name", source)
    context = record.get("context")
    if not isinstance(context, dict):
        raise TraceFormatError(f"{source}: context must be an object")
    trace_id = _required_string(context, "trace_id", source)
    span_id = _required_string(context, "span_id", source)
    parent_id = record.get("parent_id")
    if parent_id is not None and (not isinstance(parent_id, str) or not parent_id):
        raise TraceFormatError(
            f"{source}: parent_id must be null or a non-empty string"
        )
    attributes = record.get("attributes")
    if not isinstance(attributes, dict):
        raise TraceFormatError(f"{source}: attributes must be an object")
    session_id = attributes.get("session.id")
    if session_id != expected_session_id and not (
        allow_unscoped and session_id is None
    ):
        raise TraceFormatError(
            f"{source}: span session.id does not match manifest session_id"
        )
    start_time = _timestamp(record, "start_time", source)
    end_time = _timestamp(record, "end_time", source)
    if end_time < start_time:
        raise TraceFormatError(f"{source}: end_time precedes start_time")
    return TraceSpan(
        session_id=expected_session_id,
        name=name,
        trace_id=trace_id,
        span_id=span_id,
        parent_id=parent_id,
        start_time=start_time,
        end_time=end_time,
        attributes=MappingProxyType(attributes),
        raw=MappingProxyType(record),
    )


def _span_sort_key(span: TraceSpan) -> tuple[datetime, datetime, str]:
    return (span.start_time, span.end_time, span.span_id)


def _build_session(
    session_id: str, filename: str, records: list[dict[str, Any]]
) -> SessionTrace:
    spans = tuple(
        sorted(
            (
                _parse_span(
                    record,
                    expected_session_id=session_id,
                    source=f"{filename}:{line_number}",
                )
                for line_number, record in enumerate(records, start=1)
            ),
            key=_span_sort_key,
        )
    )
    by_key: dict[tuple[str, str], TraceSpan] = {}
    for span in spans:
        key = (span.trace_id, span.span_id)
        if key in by_key:
            raise TraceFormatError(f"{filename}: duplicate span id {span.span_id!r}")
        by_key[key] = span

    turn_spans = tuple(span for span in spans if span.name == "agent.turn")
    if not turn_spans:
        raise TraceFormatError(f"{filename}: session has no agent.turn span")
    turn_keys = {(span.trace_id, span.span_id) for span in turn_spans}
    descendants: dict[tuple[str, str], list[TraceSpan]] = {key: [] for key in turn_keys}

    for span in spans:
        if (span.trace_id, span.span_id) in turn_keys:
            continue
        current = span
        visited: set[tuple[str, str]] = set()
        owner: tuple[str, str] | None = None
        while current.parent_id is not None:
            parent_key = (current.trace_id, current.parent_id)
            if parent_key in visited:
                raise TraceFormatError(f"{filename}: cyclic parent relationship")
            visited.add(parent_key)
            if parent_key in turn_keys:
                owner = parent_key
                break
            parent = by_key.get(parent_key)
            if parent is None:
                break
            current = parent
        if owner is not None:
            descendants[owner].append(span)
        elif span.name in _SCORING_SPAN_NAMES or span.is_tool:
            raise TraceFormatError(
                f"{filename}: scoring span {span.name!r} is not bound to an agent.turn"
            )

    turns = tuple(
        TurnTrace(
            ordinal=ordinal,
            span=span,
            descendants=tuple(
                sorted(descendants[(span.trace_id, span.span_id)], key=_span_sort_key)
            ),
        )
        for ordinal, span in enumerate(sorted(turn_spans, key=_span_sort_key), start=1)
    )
    return SessionTrace(
        session_id=session_id,
        source_file=filename,
        spans=spans,
        turns=turns,
    )


def parse_trace_jsonl(
    manifest_jsonl: str, trace_files: Mapping[str, str]
) -> EvaluationTrace:
    """Parse exporter JSONL without relying on record completion order."""
    manifest_records = _parse_jsonl(manifest_jsonl, TRACE_MANIFEST)
    sessions: dict[str, SessionTrace] = {}
    seen_sessions: set[str] = set()
    used_files: set[str] = set()
    for line_number, record in enumerate(manifest_records, start=1):
        source = f"{TRACE_MANIFEST}:{line_number}"
        if record.get("schema_version") != TRACE_SCHEMA_VERSION:
            raise TraceFormatError(
                f"{source}: unsupported schema_version {record.get('schema_version')!r}"
            )
        session_id = _required_string(record, "session_id", source)
        filename = _manifest_filename(record.get("file"), source)
        if session_id in seen_sessions:
            raise TraceFormatError(f"{source}: duplicate session_id {session_id!r}")
        if filename in used_files:
            raise TraceFormatError(f"{source}: trace file is referenced more than once")
        try:
            content = trace_files[filename]
        except KeyError as error:
            raise TraceUnavailableError(
                f"manifest references missing trace file {filename!r}"
            ) from error
        records = _parse_jsonl(content, filename)
        if session_id == "unknown":
            scoped_records = []
            for span_line, span_record in enumerate(records, start=1):
                attributes = span_record.get("attributes")
                unscoped = (
                    isinstance(attributes, dict)
                    and attributes.get("session.id") is None
                    and span_record.get("name")
                    not in _SCORING_SPAN_NAMES | {"agent.turn"}
                    and not str(span_record.get("name", "")).startswith("tool.")
                    and attributes.get("openinference.span.kind") != "tool"
                )
                if unscoped:
                    _parse_span(
                        span_record,
                        expected_session_id=session_id,
                        source=f"{filename}:{span_line}",
                        allow_unscoped=True,
                    )
                else:
                    scoped_records.append(span_record)
            records = scoped_records
        if records:
            sessions[session_id] = _build_session(session_id, filename, records)
        seen_sessions.add(session_id)
        used_files.add(filename)
    return EvaluationTrace(sessions=MappingProxyType(sessions))


async def read_trace_input(
    environment: TraceSandbox,
    directory: PurePosixPath = TRACE_DIRECTORY,
) -> EvaluationTrace:
    """Read and parse eval traces from a sample sandbox before it is cleaned up."""
    manifest_path = (directory / TRACE_MANIFEST).as_posix()
    try:
        manifest = await environment.read_file(manifest_path)
    except Exception as error:
        raise TraceUnavailableError(
            f"trace manifest is unavailable: {manifest_path}"
        ) from error

    records = _parse_jsonl(manifest, TRACE_MANIFEST)
    files: dict[str, str] = {}
    for line_number, record in enumerate(records, start=1):
        filename = _manifest_filename(
            record.get("file"), f"{TRACE_MANIFEST}:{line_number}"
        )
        path = (directory / filename).as_posix()
        try:
            files[filename] = await environment.read_file(path)
        except Exception as error:
            raise TraceUnavailableError(
                f"manifest references unavailable trace file {filename!r}"
            ) from error
    return parse_trace_jsonl(manifest, files)
