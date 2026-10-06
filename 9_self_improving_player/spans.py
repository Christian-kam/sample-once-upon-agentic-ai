"""Record the player's OpenTelemetry spans in memory and export them in the JSON shape that
AgentCore Evaluations and AgentCore Optimization accept as inline `sessionSpans`.

Strands emits one `invoke_agent` span per turn (with the user prompt and the agent answer as span
events), one `chat` span per model call and one `execute_tool` span per tool call. We keep them all
in memory while an episode runs, then hand the session's spans straight to the Evaluate and
StartRecommendation APIs. No CloudWatch, no log groups: the whole loop runs from the laptop.
"""

import json

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from strands.telemetry import StrandsTelemetry


class SpanRecorder:
    """Install an in-memory exporter on the global Strands tracer and read spans back per session."""

    def __init__(self):
        self.exporter = InMemorySpanExporter()
        # StrandsTelemetry() registers itself as the global tracer provider used by every Agent.
        self.telemetry = StrandsTelemetry()
        self.telemetry.tracer_provider.add_span_processor(SimpleSpanProcessor(self.exporter))

    def session_spans(self, session_id: str) -> list[dict]:
        """All finished spans tagged with `session.id == session_id`, in the evaluation JSON format."""
        return [
            span_to_json(span)
            for span in self.exporter.get_finished_spans()
            if span.attributes and span.attributes.get("session.id") == session_id
        ]

    def clear(self) -> None:
        self.exporter.clear()


def _jsonable(value):
    """OpenTelemetry attribute values are str/int/float/bool or tuples of those."""
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def span_to_json(span: ReadableSpan) -> dict:
    """Convert one finished span into the record format AgentCore reads (same shape as the
    span records CloudWatch stores: ids as hex strings, times in nanoseconds, events inline)."""
    context = span.get_span_context()
    record = {
        "traceId": format(context.trace_id, "032x"),
        "spanId": format(context.span_id, "016x"),
        "name": span.name,
        "kind": span.kind.name,
        "scope": {
            "name": span.instrumentation_scope.name if span.instrumentation_scope else "",
            "version": (span.instrumentation_scope.version if span.instrumentation_scope else None) or "",
        },
        "startTimeUnixNano": span.start_time,
        "endTimeUnixNano": span.end_time,
        "durationNano": (span.end_time or 0) - (span.start_time or 0),
        "attributes": {k: _jsonable(v) for k, v in (span.attributes or {}).items()},
        "events": [
            {
                "name": event.name,
                "timeUnixNano": event.timestamp,
                "attributes": {k: _jsonable(v) for k, v in (event.attributes or {}).items()},
            }
            for event in span.events
        ],
        "status": {"code": span.status.status_code.name},
        "resource": {"attributes": {k: _jsonable(v) for k, v in span.resource.attributes.items()}},
    }
    if span.parent is not None:
        record["parentSpanId"] = format(span.parent.span_id, "016x")
    return record


def save_spans(path: str, spans: list[dict]) -> None:
    with open(path, "w") as f:
        json.dump(spans, f)


def load_spans(path: str) -> list[dict]:
    with open(path) as f:
        return json.load(f)
