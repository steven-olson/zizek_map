import json
import logging
from datetime import datetime, timezone
from typing import Any

from opentelemetry import trace

_LOGRECORD_STD_FIELDS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)


class JsonFormatter(logging.Formatter):
    """Emit each log record as one JSON object per line.

    Includes the current OTel trace_id / span_id when a span is active, so log
    lines from inside a `tracer.start_as_current_span(...)` block can be
    correlated across the pipeline's steps. Any `extra={...}` payload the caller
    passed lands under top-level keys (after filtering the standard LogRecord
    attributes), so structured fields like `book_id=...` survive into the log.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "file": record.filename,
            "line": record.lineno,
        }
        span = trace.get_current_span()
        ctx = span.get_span_context()
        if ctx.is_valid:
            payload["trace_id"] = format(ctx.trace_id, "032x")
            payload["span_id"] = format(ctx.span_id, "016x")
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key in _LOGRECORD_STD_FIELDS or key in payload:
                continue
            payload[key] = value
        return json.dumps(payload, default=str)
