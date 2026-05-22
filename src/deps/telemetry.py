import logging
import os

from opentelemetry._logs import set_logger_provider
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler, ReadableLogRecord
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor, ConsoleLogExporter
from opentelemetry.sdk.resources import Resource


class Telemetry:
    """OpenTelemetry setup for the app. Today: logs only. Spans and metrics later.

    Pipeline: Python `logging` calls → OTel LoggingHandler → LoggerProvider →
    BatchLogRecordProcessor → ConsoleLogExporter → stdout (one compact JSON line
    per record). Dozzle (mounted on the docker socket) reads container stdout and
    renders the stream in its web UI. When we eventually want a real backend, we
    add another LogRecordProcessor with an OTLPLogExporter and the Python logging
    side keeps working unchanged.
    """

    _configured: bool = False

    @classmethod
    def setup(cls, service_name: str = "zizek-map") -> None:
        """Wire the global Python logger through OpenTelemetry and export to stdout.

        Intent: idempotent and global — call once at process startup. Subsequent
        calls are no-ops so each local script can configure telemetry without
        worrying about double-attached handlers if the same process re-imports.
        """
        if cls._configured:
            return
        cls._configured = True

        resource = Resource.create({"service.name": service_name})
        provider = LoggerProvider(resource=resource)
        provider.add_log_record_processor(
            BatchLogRecordProcessor(ConsoleLogExporter(formatter=cls._format_one_line))
        )
        set_logger_provider(provider)

        otel_handler = LoggingHandler(level=logging.INFO, logger_provider=provider)
        root = logging.getLogger()
        root.setLevel(logging.INFO)
        # Replace any existing handlers (e.g. ones a prior `logging.basicConfig` added)
        # so each log record is emitted exactly once via the OTel pipeline.
        root.handlers.clear()
        root.addHandler(otel_handler)

    @staticmethod
    def _format_one_line(record: ReadableLogRecord) -> str:
        """Compact one-line JSON per OTel log record, suitable for Dozzle's stream view.

        Intent: OTel's default `record.to_json()` uses `indent=4` which produces a
        multi-line block per log — unreadable in a live tail. One-line JSON keeps
        each record on a single line while still containing every OTel attribute.
        """
        return record.to_json(indent=None) + os.linesep
