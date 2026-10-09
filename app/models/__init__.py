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

"""Domain models, enumerations, and exceptions."""

from __future__ import annotations

from app.models.enums import (
    AgentName,
    AuditEventType,
    ObservationPriority,
    TaskComplexity,
    ToolStatus,
)
from app.models.exceptions import (
    AgentDomainError,
    ExampleTaskError,
    PolicyViolationError,
    SecretAccessError,
    StorageError,
)
from app.models.schema import (
    MAX_SUMMARY_LENGTH,
    MAX_TOPIC_LENGTH,
    AuditLogEntry,
    ExampleRequest,
    ExampleResult,
    MemoryRecord,
    RemediationPlanRequest,
    RemediationPlanResult,
)

__all__ = [
    "MAX_SUMMARY_LENGTH",
    "MAX_TOPIC_LENGTH",
    "AgentDomainError",
    "AgentName",
    "AuditEventType",
    "AuditLogEntry",
    "ExampleRequest",
    "ExampleResult",
    "ExampleTaskError",
    "MemoryRecord",
    "ObservationPriority",
    "PolicyViolationError",
    "RemediationPlanRequest",
    "RemediationPlanResult",
    "SecretAccessError",
    "StorageError",
    "TaskComplexity",
    "ToolStatus",
]
