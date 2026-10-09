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

"""Canonical session.state keys shared across agents, tools, and plugins."""

from __future__ import annotations

from typing import Final

LAST_EXAMPLE_RESULT_KEY: Final[str] = "last_example_result"
"""Session-scoped result of the most recent topic analysis tool call."""

LAST_REMEDIATION_PLAN_KEY: Final[str] = "last_remediation_plan"
"""Session-scoped result of the most recent remediation plan tool call."""

LAST_RETRIEVED_MEMORIES_KEY: Final[str] = "last_retrieved_memories"
"""Session-scoped list of memories retrieved from the persistent memory service."""

PENDING_HITL_ACTION_KEY: Final[str] = "pending_hitl_action"
"""Session-scoped record of a high-stakes tool call paused awaiting human confirmation."""

HITL_APPROVED_ACTIONS_KEY: Final[str] = "user:hitl_approved_actions"
"""User-scoped list of high-stakes tool approvals explicitly confirmed by a human reviewer."""

OBSERVATION_LOG_KEY: Final[str] = "user:observation_log"
"""User-scoped list of PII-scrubbed memory observations consolidated across turns."""

INTENT_OUTCOME_TRACE_KEY: Final[str] = "temp:intent_outcome_trace"
"""Turn-scoped list of structured intent and outcome audit entries."""

ACTIVE_TOOL_CORRELATIONS_KEY: Final[str] = "temp:active_tool_correlations"
"""Turn-scoped mapping of active tool invocations to their correlation IDs."""

SELF_EVAL_STATUS_KEY: Final[str] = "temp:self_eval_status"
"""Turn-scoped status of the post-model guardrail self-evaluation check."""

GUARDRAIL_BLOCKED_KEY: Final[str] = "temp:guardrail_blocked"
"""Turn-scoped flag indicating that before_model_callback blocked a prompt-injection request."""

LAST_CONSOLIDATED_EVENT_ID_KEY: Final[str] = "last_consolidated_event_id"
"""Session-scoped identifier of the most recent user event consolidated into memory."""

ROUTED_MODEL_KEY: Final[str] = "routed_model"
"""Session-scoped identifier of the model tier selected for the active task."""
