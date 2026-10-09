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

"""Domain enumerations for agent names, tool statuses, and audit events."""

from __future__ import annotations

import enum


class AgentName(enum.StrEnum):
    """Canonical ADK agent names; ADK requires them to be valid Python identifiers."""

    ORCHESTRATOR = "orchestrator_agent"
    SPECIALIST = "specialist_agent"
    PLANNER = "planner_agent"
    SEQUENTIAL_WORKFLOW = "sequential_workflow_agent"


class ToolStatus(enum.StrEnum):
    """Status values returned to the LLM in every tool response payload."""

    SUCCESS = "success"
    ERROR = "error"
    AWAITING_CONFIRMATION = "awaiting_confirmation"


class TaskComplexity(enum.StrEnum):
    """Task complexity tiers used for strategic model routing (Flash vs. Pro)."""

    ROUTINE = "routine"
    COMPLEX = "complex"


class ObservationPriority(enum.StrEnum):
    """Priority tiers for extracted session memory observations."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class AuditEventType(enum.StrEnum):
    """Structured observability event categories capturing intent, outcome, and guardrails."""

    AGENT_INTENT = "agent_intent"
    AGENT_OUTCOME = "agent_outcome"
    POLICY_BLOCK = "policy_block"
    SELF_EVAL = "self_eval"


class SeverityTier(enum.StrEnum):
    """Support case severity tiers produced by runbook diagnosis."""

    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class LogSeverity(enum.StrEnum):
    """Severity levels used by structured audit log entries."""

    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
