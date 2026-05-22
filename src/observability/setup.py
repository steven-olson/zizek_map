import logging
import sys

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider

from src.observability.json_formatter import JsonFormatter

_DEFAULT_FORMAT_INSTALLED = False


def configure_observability(*, service_name: str, log_level: str) -> None:
    """One-shot startup wiring of OpenTelemetry tracing + JSON stdout logging.

    Intent: every entrypoint (Textual UI, CLI, alembic env.py) calls this once
    before any other work. After it runs, anything the codebase logs lands as
    one JSON object per line on stdout — which Dozzle renders, and which any
    `trace.get_current_span()`-aware tool can correlate via `trace_id`.

    A TracerProvider is installed even though we don't export spans (no Jaeger /
    OTel Collector wired up yet). Without a provider, `trace.get_current_span()`
    returns INVALID_SPAN unconditionally and the trace-context fields stay
    empty even inside `start_as_current_span` blocks.
    """
    global _DEFAULT_FORMAT_INSTALLED
    if _DEFAULT_FORMAT_INSTALLED:
        return

    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    trace.set_tracer_provider(provider)

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(log_level)

    _DEFAULT_FORMAT_INSTALLED = True
