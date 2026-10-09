# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Structured JSON logging, Intent vs. Outcome capture, and OpenTelemetry tracing.

Integrates OpenTelemetry spans, `StructuredJsonFormatter`, and `PiiRedactionService`
so every log line, agent intent, and tool outcome is traced and scrubbed before emission.
"""

from __future__ import annotations

import collections
import contextlib
import datetime
import json
import logging
import os
from collections.abc import Iterator
from typing import Final, TextIO

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider as SdkTracerProvider
from opentelemetry.trace import Span, Status, StatusCode

from app.models.enums import AuditEventType, LogSeverity, ToolStatus
from app.models.schema import AuditLogEntry
from app.services.redaction import PiiRedactionService

LABELS_KEY: Final[str] = "logging.googleapis.com/labels"
TRACE_KEY: Final[str] = "logging.googleapis.com/trace"
SPAN_ID_KEY: Final[str] = "logging.googleapis.com/spanId"
MAX_AUDIT_ENTRIES: Final[int] = 1000
_PRE_SERIALIZED_ATTR: Final[str] = "pre_serialized_json"

_logger: Final[logging.Logger] = logging.getLogger(__name__)


def _resolve_project_id(project_id: str = "") -> str:
    """Resolves an explicit project ID or falls back to `GOOGLE_CLOUD_PROJECT`."""
    return project_id.strip() or os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()


def _ensure_tracer() -> trace.Tracer:
    """Lazily ensures an SDK `TracerProvider` is registered without clobbering runtime setup."""
    provider = trace.get_tracer_provider()
    if type(provider).__name__ in {"ProxyTracerProvider", "NoOpTracerProvider"}:
        trace.set_tracer_provider(SdkTracerProvider())
    return trace.get_tracer("adk_starter.observability")


def current_trace_context() -> tuple[str, str]:
    """Extracts the active OpenTelemetry `(trace_id, span_id)` in hex format."""
    span: Span = trace.get_current_span()
    span_ctx = span.get_span_context()
    if not span_ctx.is_valid:
        return "", ""
    return f"{span_ctx.trace_id:032x}", f"{span_ctx.span_id:016x}"


def _attach_trace_context(
    payload: dict[str, object],
    project_id: str,
) -> tuple[str, str]:
    """Attaches active trace and span identifiers to `payload` and returns `(trace_id, span_id)`."""
    trace_id, span_id = current_trace_context()
    if trace_id:
        payload[TRACE_KEY] = f"projects/{project_id}/traces/{trace_id}" if project_id else trace_id
    if span_id:
        payload[SPAN_ID_KEY] = span_id
    return trace_id, span_id


class StructuredJsonFormatter(logging.Formatter):
    """Formats standard Python `LogRecord` objects as PII-scrubbed structured JSON lines."""

    def __init__(self, *, project_id: str = "") -> None:
        super().__init__()
        self._project_id: str = _resolve_project_id(project_id)
        self._redactor: PiiRedactionService = PiiRedactionService(project_id=self._project_id)

    def format(self, record: logging.LogRecord) -> str:
        """Serializes `record` into a single-line JSON string with trace context."""
        if getattr(record, _PRE_SERIALIZED_ATTR, False):
            return record.getMessage()

        raw_message = record.getMessage()
        clean_message, _ = self._redactor.redact_text(raw_message)
        payload: dict[str, object] = {
            "severity": record.levelname,
            "message": clean_message,
            "logger": record.name,
            "time": datetime.datetime.now(datetime.UTC).isoformat(),
        }
        _attach_trace_context(payload, self._project_id)
        return json.dumps(payload, default=str)


def configure_structured_logging(*, project_id: str = "", level: int = logging.INFO) -> None:
    """Attaches `StructuredJsonFormatter` to the `app` package logger hierarchy."""
    resolved_project = _resolve_project_id(project_id)
    app_logger = logging.getLogger("app")
    app_logger.setLevel(level)
    app_logger.propagate = False
    for existing in app_logger.handlers:
        fmt = getattr(existing, "formatter", None)
        if isinstance(fmt, StructuredJsonFormatter):
            if resolved_project and not fmt._project_id:
                existing.setFormatter(StructuredJsonFormatter(project_id=resolved_project))
            return
    handler = logging.StreamHandler()
    handler.setFormatter(StructuredJsonFormatter(project_id=resolved_project))
    app_logger.addHandler(handler)


class StructuredAuditLogger:
    """Emits structured JSON audit entries with OpenTelemetry trace context and PII scrubbing."""

    def __init__(
        self,
        *,
        redactor: PiiRedactionService | None = None,
        project_id: str = "",
        stream: TextIO | None = None,
        max_entries: int = MAX_AUDIT_ENTRIES,
    ) -> None:
        """Initializes the structured audit logger.

        Args:
            redactor: PII redaction service applied to every log message and detail field.
            project_id: Optional project ID used to format trace resource paths.
            stream: Optional text stream receiving raw JSON lines (in addition to standard logger).
            max_entries: Maximum in-memory audit entries retained in the ring buffer.
        """
        resolved_project = _resolve_project_id(project_id)
        self._redactor: PiiRedactionService = redactor or PiiRedactionService(
            project_id=resolved_project
        )
        self._project_id: str = resolved_project
        self._stream: TextIO | None = stream
        self._entries: collections.deque[AuditLogEntry] = collections.deque(maxlen=max_entries)

    @property
    def entries(self) -> tuple[AuditLogEntry, ...]:
        """Returns an immutable snapshot of emitted audit entries for inspection and tests."""
        return tuple(self._entries)

    @contextlib.contextmanager
    def start_span(
        self,
        span_name: str,
        *,
        attributes: dict[str, str] | None = None,
    ) -> Iterator[Span]:
        """Opens a linked OpenTelemetry span and records attribute metadata."""
        tracer = _ensure_tracer()
        with tracer.start_as_current_span(span_name) as span:
            if attributes:
                for key, value in attributes.items():
                    clean_value, _ = self._redactor.redact_text(value)
                    span.set_attribute(key, clean_value)
            try:
                yield span
            except Exception as exc:
                clean_exc, _ = self._redactor.redact_text(str(exc))
                span.set_status(Status(StatusCode.ERROR, clean_exc))
                raise

    def emit(
        self,
        severity: LogSeverity | str,
        message: str,
        *,
        event_type: AuditEventType,
        correlation_id: str,
        tool_name: str = "",
        status: ToolStatus | None = None,
        labels: dict[str, str] | None = None,
        details: dict[str, object] | None = None,
    ) -> AuditLogEntry:
        """Scrubs PII and emits a single structured JSON audit entry."""
        clean_message, _ = self._redactor.redact_text(message)
        clean_details, _ = self._redactor.redact_mapping(details or {})
        trace_id, span_id = current_trace_context()
        now_iso = datetime.datetime.now(datetime.UTC).isoformat()
        resolved_severity = (
            severity if isinstance(severity, LogSeverity) else LogSeverity(severity.upper())
        )

        entry = AuditLogEntry(
            severity=resolved_severity,
            message=clean_message,
            event_type=event_type,
            correlation_id=correlation_id,
            trace_id=trace_id,
            span_id=span_id,
            tool_name=tool_name,
            status=status,
            timestamp_utc=now_iso,
        )
        self._entries.append(entry)

        payload: dict[str, object] = {
            "severity": entry.severity.value,
            "message": entry.message,
            "time": entry.timestamp_utc,
            "event_type": entry.event_type.value,
            "correlation_id": entry.correlation_id,
            "tool_name": entry.tool_name,
            "status": entry.status.value if entry.status is not None else None,
            "details": clean_details,
        }
        _attach_trace_context(payload, self._project_id)
        if labels:
            payload[LABELS_KEY] = {
                str(k): self._redactor.redact_text(str(v))[0] for k, v in labels.items()
            }

        serialized = json.dumps(payload, default=str)
        log_level = getattr(logging, entry.severity.value, logging.INFO)
        _logger.log(log_level, "%s", serialized, extra={_PRE_SERIALIZED_ATTR: True})
        if self._stream is not None:
            self._stream.write(serialized + "\n")
            self._stream.flush()
        return entry

    def record_intent(
        self,
        *,
        correlation_id: str,
        tool_name: str,
        tool_args: dict[str, object],
    ) -> AuditLogEntry:
        """Records the agent's intended tool invocation before execution."""
        return self.emit(
            LogSeverity.INFO,
            f"Agent intent: invoking tool {tool_name}",
            event_type=AuditEventType.AGENT_INTENT,
            correlation_id=correlation_id,
            tool_name=tool_name,
            labels={"event_type": AuditEventType.AGENT_INTENT.value, "tool": tool_name},
            details={"tool_args": tool_args},
        )

    def record_outcome(
        self,
        *,
        correlation_id: str,
        tool_name: str,
        status: ToolStatus,
        result_summary: dict[str, object],
    ) -> AuditLogEntry:
        """Records the actual tool execution outcome paired with its prior intent."""
        severity = LogSeverity.WARNING if status == ToolStatus.ERROR else LogSeverity.INFO
        return self.emit(
            severity,
            f"Agent outcome: tool {tool_name} finished with status={status.value}",
            event_type=AuditEventType.AGENT_OUTCOME,
            correlation_id=correlation_id,
            tool_name=tool_name,
            status=status,
            labels={"event_type": AuditEventType.AGENT_OUTCOME.value, "tool": tool_name},
            details={"result": result_summary},
        )
