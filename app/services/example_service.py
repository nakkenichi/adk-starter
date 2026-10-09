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

"""Cloud support symptom diagnosis and critical escalation planning service.

Services hold pure business logic: they take and return Pydantic models, raise
AgentDomainError subclasses for expected failures, and remain independent of the
agent runtime so they can be unit-tested in isolation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from app.models.enums import SeverityTier
from app.models.schema import (
    ExampleRequest,
    ExampleResult,
    RemediationPlanRequest,
    RemediationPlanResult,
)


def _matches_keyword(text: str, keyword: str) -> bool:
    """Returns True when `keyword` matches `text` on word boundaries."""
    return bool(re.search(rf"\b{re.escape(keyword)}\b", text))


@dataclass(frozen=True)
class _SupportRunbook:
    """Internal definition of a deterministic support troubleshooting runbook."""

    runbook_id: str
    severity_tier: SeverityTier
    title: str
    keywords: tuple[str, ...]
    actions: tuple[str, ...]


_SUPPORT_RUNBOOKS: Final[tuple[_SupportRunbook, ...]] = (
    _SupportRunbook(
        runbook_id="RUNBOOK-DB-309",
        severity_tier=SeverityTier.P1,
        title="Database Connection Pool Exhaustion & Deadlock Mitigation",
        keywords=("database", "postgres", "sql", "deadlock", "connection pool", "replica"),
        actions=(
            "Inspect active connection counts and long-running transactions.",
            "Verify PgBouncer or Cloud SQL connection pooler saturation metrics.",
            "Prepare a controlled read-replica promotion or connection drain if saturated.",
        ),
    ),
    _SupportRunbook(
        runbook_id="RUNBOOK-NET-101",
        severity_tier=SeverityTier.P2,
        title="Ingress Gateway Timeout & P99 Latency Triage",
        keywords=("latency", "timeout", "504", "502", "503", "ingress", "gke", "load balancer"),
        actions=(
            "Check Kubernetes cluster backend service health checks and pod readiness probes.",
            "Correlate p99 latency spikes with recent HPA scaling events or node pressure.",
            "Escalate to Tier-3 if upstream packet loss or unhealthy node pools require draining.",
        ),
    ),
    _SupportRunbook(
        runbook_id="RUNBOOK-IAM-204",
        severity_tier=SeverityTier.P2,
        title="IAM Permission Denied & Workload Identity Federation Triage",
        keywords=("403", "permission", "iam", "unauthorized", "token", "service account", "adc"),
        actions=(
            "Verify IAM role bindings on the workload service account.",
            "Check audit logs for policy denials on target resources.",
            "Confirm secret manager and key accessor bindings match the runtime identity.",
        ),
    ),
    _SupportRunbook(
        runbook_id="RUNBOOK-QUOTA-412",
        severity_tier=SeverityTier.P3,
        title="API Rate Limiting & Regional Quota Exhaustion",
        keywords=("429", "quota", "rate limit", "resource_exhausted", "throttling", "capacity"),
        actions=(
            "Inspect regional requests-per-minute consumption in service monitoring.",
            "Enable exponential backoff with jitter on client retry policies.",
            "Submit a quota increase request or route overflow traffic to secondary regions.",
        ),
    ),
)

_DEFAULT_RUNBOOK: Final[_SupportRunbook] = _SupportRunbook(
    runbook_id="RUNBOOK-GEN-001",
    severity_tier=SeverityTier.P3,
    title="General Enterprise Cloud Support Triage",
    keywords=(),
    actions=(
        "Inspect service monitoring dashboards and OpenTelemetry trace spans for anomalies.",
        "Review recent deployment or configuration changes within the incident window.",
        "Escalate to Tier-3 engineering if customer production SLAs are impacted.",
    ),
)


class ExampleService:
    """Deterministic domain service for cloud support symptom diagnosis and escalation planning."""

    def run(self, request: ExampleRequest) -> ExampleResult:
        """Diagnoses a reported support symptom against enterprise cloud runbooks.

        Args:
            request: Validated support symptom input.

        Returns:
            Structured `ExampleResult` with matched runbook, severity tier, and action checklist.
        """
        word_count = len(request.topic.split())
        lowered = request.topic.lower()
        matched = _DEFAULT_RUNBOOK
        for candidate in _SUPPORT_RUNBOOKS:
            if any(_matches_keyword(lowered, keyword) for keyword in candidate.keywords):
                matched = candidate
                break

        return ExampleResult(
            topic=request.topic,
            summary=(
                f"[{matched.severity_tier} | {matched.runbook_id}] {matched.title} "
                f"for reported symptom {request.topic!r} ({word_count} words)."
            ),
            word_count=word_count,
            matched_runbook=matched.runbook_id,
            severity_tier=matched.severity_tier,
            recommended_actions=matched.actions,
        )

    def create_remediation_plan(
        self,
        request: RemediationPlanRequest,
        *,
        routed_model: str,
    ) -> RemediationPlanResult:
        """Builds a validated critical escalation and remediation record for a support incident.

        Args:
            request: Validated escalation/remediation parameters.
            routed_model: Identifier of the model tier used for planning.

        Returns:
            Structured `RemediationPlanResult`.
        """
        return RemediationPlanResult(
            incident_id=request.incident_id,
            target_environment=request.target_environment,
            action_summary=request.action_summary,
            routed_model=routed_model,
        )
