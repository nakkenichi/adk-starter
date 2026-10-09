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

"""Tests for the example, memory retrieval, and high-stakes remediation tools."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from google.adk.tools.function_tool import FunctionTool

from app.agents.state import (
    LAST_EXAMPLE_RESULT_KEY,
    LAST_REMEDIATION_PLAN_KEY,
    LAST_RETRIEVED_MEMORIES_KEY,
    OBSERVATION_LOG_KEY,
)
from app.models.enums import ToolStatus
from app.models.exceptions import ExampleTaskError
from app.models.schema import MAX_TOPIC_LENGTH
from app.tools.example_tools import (
    create_critical_escalation_bug,
    diagnose_support_case_symptom,
    retrieve_customer_support_memories,
    run_example_task,
)


def _tool_context(
    initial_state: dict[str, object] | None = None,
    *,
    user_id: str = "u-1",
) -> MagicMock:
    """Builds a minimal ToolContext stand-in with a real dict as session state."""
    context = MagicMock()
    context.state = dict(initial_state or {})
    context.user_id = user_id
    return context


@pytest.mark.parametrize(
    ("topic", "expected_topic", "expected_word_count", "expected_runbook"),
    [
        pytest.param(
            "GKE ingress 504 timeout",
            "GKE ingress 504 timeout",
            4,
            "RUNBOOK-NET-101",
            id="network_timeout",
        ),
        pytest.param(
            "  postgres deadlock error  ",
            "postgres deadlock error",
            3,
            "RUNBOOK-DB-309",
            id="padded_db_deadlock",
        ),
    ],
)
def test_diagnose_support_case_symptom_returns_and_stores_result(
    topic: str,
    expected_topic: str,
    expected_word_count: int,
    expected_runbook: str,
) -> None:
    context = _tool_context()

    response = diagnose_support_case_symptom(topic, tool_context=context)

    assert response["status"] == ToolStatus.SUCCESS.value
    result = response["result"]
    assert isinstance(result, dict)
    assert result["topic"] == expected_topic
    assert result["word_count"] == expected_word_count
    assert result["matched_runbook"] == expected_runbook
    assert context.state[LAST_EXAMPLE_RESULT_KEY] == result


@pytest.mark.parametrize("topic", ["", "   ", "x" * (MAX_TOPIC_LENGTH + 1)])
def test_run_example_task_rejects_invalid_topics(topic: str) -> None:
    context = _tool_context()

    with pytest.raises(ExampleTaskError) as exc_info:
        run_example_task(topic, tool_context=context)

    assert exc_info.value.recovery_hint
    assert LAST_EXAMPLE_RESULT_KEY not in context.state


def test_retrieve_customer_support_memories_returns_matching_user_records() -> None:
    context = _tool_context(
        {
            OBSERVATION_LOG_KEY: [
                {
                    "user_id": "u-1",
                    "session_id": "s-1",
                    "fact": "Primary region is asia-northeast1",
                    "priority": "high",
                    "timestamp_utc": "2026-10-08T00:00:00+00:00",
                }
            ]
        },
        user_id="u-1",
    )

    response = retrieve_customer_support_memories(
        "u-1", "region asia-northeast1", tool_context=context
    )

    assert response["status"] == ToolStatus.SUCCESS.value
    memories = response["memories"]
    assert isinstance(memories, list)
    assert len(memories) == 1
    assert context.state[LAST_RETRIEVED_MEMORIES_KEY] == memories


def test_retrieve_customer_support_memories_raises_on_corrupted_observation_log() -> None:
    context = _tool_context(
        {OBSERVATION_LOG_KEY: [{"corrupted": "entry"}]},
        user_id="u-1",
    )

    with pytest.raises(ExampleTaskError):
        retrieve_customer_support_memories("u-1", "region asia-northeast1", tool_context=context)


def test_retrieve_customer_support_memories_rejects_cross_tenant_user_id() -> None:
    from app.models.exceptions import PolicyViolationError

    context = _tool_context(user_id="u-1")
    with pytest.raises(PolicyViolationError):
        retrieve_customer_support_memories(
            "u-other-tenant",
            "region asia-northeast1",
            tool_context=context,
        )


def test_create_critical_escalation_bug_validates_and_stores_plan() -> None:
    context = _tool_context()

    response = create_critical_escalation_bug(
        "INC-2048",
        "Drain and cordon unhealthy node pool",
        "production",
        tool_context=context,
    )

    assert response["status"] == ToolStatus.SUCCESS.value
    assert context.state[LAST_REMEDIATION_PLAN_KEY]["incident_id"] == "INC-2048"


def test_declaration_exposes_only_llm_arguments() -> None:
    declaration = FunctionTool(diagnose_support_case_symptom)._get_declaration()

    assert declaration is not None
    assert declaration.name == "diagnose_support_case_symptom"
    schema = declaration.parameters_json_schema
    assert isinstance(schema, dict)
    assert schema["required"] == ["topic"]
    assert set(schema["properties"]) == {"topic"}
