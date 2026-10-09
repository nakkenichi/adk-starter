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

"""Unit tests for StructuredAuditLogger, Intent vs. Outcome capture, and OTel spans."""

from __future__ import annotations

import io
import json
import logging

from app.models.enums import AuditEventType, ToolStatus
from app.services.observability import StructuredAuditLogger, StructuredJsonFormatter
from app.services.redaction import REDACTED_EMAIL


def test_record_intent_and_outcome_emit_structured_json_with_pii_redaction() -> None:
    stream = io.StringIO()
    logger = StructuredAuditLogger(project_id="demo-gcp-project", stream=stream)

    with logger.start_span("test.turn", attributes={"user.email": "ops@example.com"}):
        intent_entry = logger.record_intent(
            correlation_id="corr-123",
            tool_name="analyze_incident_telemetry",
            tool_args={"topic": "Alert for ops@example.com"},
        )
        outcome_entry = logger.record_outcome(
            correlation_id="corr-123",
            tool_name="analyze_incident_telemetry",
            status=ToolStatus.SUCCESS,
            result_summary={"status": "success", "owner": "ops@example.com"},
        )

    assert intent_entry.event_type == AuditEventType.AGENT_INTENT
    assert outcome_entry.event_type == AuditEventType.AGENT_OUTCOME
    assert intent_entry.correlation_id == outcome_entry.correlation_id == "corr-123"
    assert len(logger.entries) == 2

    lines = [json.loads(line) for line in stream.getvalue().strip().splitlines()]
    assert len(lines) == 2
    assert lines[0]["event_type"] == "agent_intent"
    assert lines[0]["details"]["tool_args"]["topic"] == f"Alert for {REDACTED_EMAIL}"
    assert lines[1]["event_type"] == "agent_outcome"
    assert lines[1]["details"]["result"]["owner"] == REDACTED_EMAIL


def test_structured_json_formatter_redacts_pii_and_formats_cloud_logging_fields() -> None:
    formatter = StructuredJsonFormatter(project_id="demo-gcp-project")
    audit_logger = StructuredAuditLogger(project_id="demo-gcp-project", stream=io.StringIO())
    record = logging.LogRecord(
        name="app.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=42,
        msg="Escalating alert for user %s",
        args=("admin@example.com",),
        exc_info=None,
    )

    with audit_logger.start_span("test.formatter.span"):
        payload = json.loads(formatter.format(record))

    assert payload["severity"] == "INFO"
    assert payload["logger"] == "app.test"
    assert payload["message"] == f"Escalating alert for user {REDACTED_EMAIL}"
    assert "logging.googleapis.com/trace" in payload
