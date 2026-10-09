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

"""Pydantic domain models exchanged between tools and services."""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    AuditEventType,
    LogSeverity,
    ObservationPriority,
    SeverityTier,
    TaskComplexity,
    ToolStatus,
)

MAX_TOPIC_LENGTH: Final[int] = 200
MAX_SUMMARY_LENGTH: Final[int] = 500


class ExampleRequest(BaseModel):
    """Validated input of the topic analysis task."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    topic: str = Field(
        min_length=1,
        max_length=MAX_TOPIC_LENGTH,
        description="Short description of what the user wants analysed.",
    )
    complexity: TaskComplexity = Field(
        default=TaskComplexity.ROUTINE,
        description="Task complexity tier used for routing between Flash and Pro models.",
    )


class ExampleResult(BaseModel):
    """Structured output of a cloud support symptom diagnosis."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    topic: str = Field(description="Normalized support symptom or topic that was diagnosed.")
    summary: str = Field(description="Human-readable diagnostic summary and runbook match.")
    word_count: int = Field(ge=1, description="Number of words in the reported symptom.")
    matched_runbook: str = Field(
        default="RUNBOOK-GEN-001",
        description="Identifier of the matched support engineering runbook.",
    )
    severity_tier: SeverityTier = Field(
        default=SeverityTier.P3,
        description="Classified support case severity tier (P1, P2, or P3).",
    )
    recommended_actions: tuple[str, ...] = Field(
        default=(),
        description="Ordered troubleshooting or mitigation steps from the matched runbook.",
    )


class RemediationPlanRequest(BaseModel):
    """Validated input schema for publishing a high-stakes remediation plan."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    incident_id: str = Field(
        min_length=3,
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="Unique incident identifier (alphanumeric, hyphens, underscores).",
    )
    action_summary: str = Field(
        min_length=5,
        max_length=MAX_SUMMARY_LENGTH,
        description="Concrete remediation steps to execute.",
    )
    target_environment: str = Field(
        min_length=2,
        max_length=32,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="Target deployment environment (e.g., staging, production).",
    )


class RemediationPlanResult(BaseModel):
    """Validated output schema returned when a remediation plan is recorded."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    incident_id: str = Field(description="Incident identifier.")
    target_environment: str = Field(description="Target environment.")
    action_summary: str = Field(description="Approved remediation summary.")
    routed_model: str = Field(description="Model tier selected for the remediation plan.")


class MemoryLookupRequest(BaseModel):
    """Validated input schema for retrieving scope-isolated customer support memories."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    user_id: str = Field(
        min_length=1,
        max_length=128,
        description="Unique customer or tenant identifier whose memory scope is queried.",
    )
    query: str = Field(
        min_length=1,
        max_length=MAX_TOPIC_LENGTH,
        description="Natural-language search query used to rank relevant stored observations.",
    )


class MemoryRecord(BaseModel):
    """Atomic, PII-scrubbed observation stored in the persistent memory service."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    user_id: str = Field(min_length=1, description="Owner user identifier for scope isolation.")
    session_id: str = Field(min_length=1, description="Source session identifier.")
    fact: str = Field(min_length=1, description="PII-redacted atomic fact or observation.")
    priority: ObservationPriority = Field(
        default=ObservationPriority.HIGH,
        description="Importance priority of the memory record.",
    )
    timestamp_utc: str = Field(description="ISO-8601 UTC timestamp when the memory was recorded.")


class DiagnosisToolResponse(BaseModel):
    """Validated output envelope returned by `diagnose_support_case_symptom`."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    status: ToolStatus = Field(default=ToolStatus.SUCCESS, description="Tool execution status.")
    result: ExampleResult = Field(description="Validated runbook diagnostic payload.")


class MemoryLookupToolResponse(BaseModel):
    """Validated output envelope returned by `retrieve_customer_support_memories`."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    status: ToolStatus = Field(default=ToolStatus.SUCCESS, description="Tool execution status.")
    memories: tuple[MemoryRecord, ...] = Field(
        default=(),
        description="Ordered, scope-isolated customer memory records matching the query.",
    )


class RemediationToolResponse(BaseModel):
    """Validated output envelope returned by high-stakes escalation and remediation tools."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    status: ToolStatus = Field(default=ToolStatus.SUCCESS, description="Tool execution status.")
    plan: RemediationPlanResult = Field(description="Validated escalation/remediation plan.")


class AuditLogEntry(BaseModel):
    """Structured JSON audit entry capturing agent intent vs. outcome and OTel trace context."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    severity: LogSeverity = Field(
        default=LogSeverity.INFO,
        description="Structured log severity level (INFO, WARNING, ERROR).",
    )
    message: str = Field(description="PII-scrubbed human-readable log summary.")
    event_type: AuditEventType = Field(description="Intent, outcome, policy_block, or self_eval.")
    correlation_id: str = Field(description="Identifier linking an intent entry to its outcome.")
    trace_id: str = Field(default="", description="OpenTelemetry trace ID in hex format.")
    span_id: str = Field(default="", description="OpenTelemetry span ID in hex format.")
    tool_name: str = Field(default="", description="Tool or agent subject of the event.")
    status: ToolStatus | None = Field(default=None, description="Outcome status when applicable.")
    timestamp_utc: str = Field(description="ISO-8601 UTC timestamp.")
