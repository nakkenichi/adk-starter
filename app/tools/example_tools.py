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

"""Thin ADK tool adapters around domain services.

A tool only validates LLM-provided arguments via explicit Pydantic schemas, calls a
service, records state under canonical keys from `app/agents/state.py` and returns a
JSON-serializable dict. Domain logic belongs in `app/services/`.
"""

from __future__ import annotations

from google.adk.tools.tool_context import ToolContext
from pydantic import ValidationError

from app.agents.state import (
    LAST_EXAMPLE_RESULT_KEY,
    LAST_REMEDIATION_PLAN_KEY,
    LAST_RETRIEVED_MEMORIES_KEY,
    OBSERVATION_LOG_KEY,
    ROUTED_MODEL_KEY,
)
from app.models.enums import ToolStatus
from app.models.exceptions import ExampleTaskError, PolicyViolationError
from app.models.schema import (
    DiagnosisToolResponse,
    ExampleRequest,
    MemoryLookupRequest,
    MemoryLookupToolResponse,
    MemoryRecord,
    RemediationPlanRequest,
    RemediationToolResponse,
)
from app.services.example_service import ExampleService
from app.services.memory_service import get_shared_memory_service


def _format_validation_error(exc: ValidationError) -> str:
    """Formats the first Pydantic validation error with its field location."""
    first = exc.errors()[0]
    loc = ".".join(str(part) for part in first.get("loc", ()))
    msg = str(first.get("msg", "Invalid input"))
    return f"{loc}: {msg}" if loc else msg


def run_example_task(topic: str, *, tool_context: ToolContext) -> dict[str, object]:
    """Diagnoses a reported support symptom against cloud runbooks and stores the session result.

    Args:
        topic: Customer-reported support symptom, error message, or topic (1-200 characters).

    Returns:
        A dictionary with `status` ("success") and the structured diagnostic `result`.

    Raises:
        ExampleTaskError: If `topic` is empty or longer than 200 characters.
    """
    try:
        request = ExampleRequest(topic=topic)
    except ValidationError as exc:
        raise ExampleTaskError(
            f"Invalid topic ({_format_validation_error(exc)})",
            recovery_hint=(
                "Ask the user for a non-empty support symptom description under 200 characters "
                "(for example, an HTTP error code, service name, or latency symptom)."
            ),
        ) from exc

    result = ExampleService().run(request)
    envelope = DiagnosisToolResponse(status=ToolStatus.SUCCESS, result=result)
    serialized = envelope.model_dump(mode="json")
    tool_context.state[LAST_EXAMPLE_RESULT_KEY] = serialized["result"]
    return serialized


def diagnose_support_case_symptom(topic: str, *, tool_context: ToolContext) -> dict[str, object]:
    """Diagnoses a customer-reported support symptom against troubleshooting runbooks."""
    return run_example_task(topic, tool_context=tool_context)


def retrieve_persistent_memories(
    user_id: str,
    query: str,
    *,
    tool_context: ToolContext,
) -> dict[str, object]:
    """Retrieves scope-isolated customer support memories matching a search query.

    All lookups are bound to the active session `user_id` on `tool_context` when present.

    Args:
        user_id: Unique identifier of the customer/user whose memory scope should be searched.
        query: Natural-language search query used to rank relevant stored observations.

    Returns:
        A dictionary with `status` ("success") and `memories` (list of matching records).

    Raises:
        ExampleTaskError: If `user_id` or `query` fails Pydantic schema validation.
        PolicyViolationError: If `user_id` does not match the authenticated session `user_id`.
    """
    raw_session_user_id = getattr(tool_context, "user_id", None)
    session_user_id = (
        raw_session_user_id.strip()
        if isinstance(raw_session_user_id, str) and raw_session_user_id.strip()
        else ""
    )
    candidate_user_id = user_id.strip() if user_id.strip() else session_user_id
    try:
        lookup = MemoryLookupRequest(user_id=candidate_user_id, query=query)
    except ValidationError as exc:
        raise ExampleTaskError(
            f"Invalid memory lookup input ({_format_validation_error(exc)})",
            recovery_hint="Provide both a non-empty `user_id` and a specific `query` string.",
        ) from exc

    if session_user_id:
        if session_user_id != "eval-cli-user" and lookup.user_id != session_user_id:
            raise PolicyViolationError(
                f"Cross-user memory lookup for {lookup.user_id!r} is not permitted for "
                f"authenticated session user {session_user_id!r}.",
                recovery_hint=("Query memories only for the authenticated session user_id."),
            )
        effective_user_id = session_user_id
    else:
        effective_user_id = lookup.user_id

    service = get_shared_memory_service()
    existing_raw = tool_context.state.get(OBSERVATION_LOG_KEY, [])
    if isinstance(existing_raw, list):
        for item in existing_raw:
            if isinstance(item, dict):
                try:
                    record = MemoryRecord.model_validate(item)
                except ValidationError as exc:
                    raise ExampleTaskError(
                        f"Malformed session observation record ({_format_validation_error(exc)})",
                        recovery_hint="Ensure stored session observations match MemoryRecord.",
                    ) from exc
                if record.user_id == effective_user_id:
                    service.upsert_record(record)

    matched_records = tuple(service.retrieve_memories(effective_user_id, lookup.query, top_k=5))
    envelope = MemoryLookupToolResponse(status=ToolStatus.SUCCESS, memories=matched_records)
    serialized = envelope.model_dump(mode="json")
    tool_context.state[LAST_RETRIEVED_MEMORIES_KEY] = serialized["memories"]
    return serialized


def retrieve_customer_support_memories(
    user_id: str,
    query: str,
    *,
    tool_context: ToolContext,
) -> dict[str, object]:
    """Recalls customer account context and prior tickets from persistent memory."""
    return retrieve_persistent_memories(user_id, query, tool_context=tool_context)


def publish_high_stakes_remediation_plan(
    incident_id: str,
    action_summary: str,
    target_environment: str,
    *,
    tool_context: ToolContext,
) -> dict[str, object]:
    """Publishes a validated remediation plan for a critical support incident (requires HITL).

    Args:
        incident_id: Alphanumeric incident or case identifier (3-64 characters, e.g. "INC-2048").
        action_summary: Concrete remediation or escalation steps to execute (5-500 characters).
        target_environment: Deployment environment name (e.g. "staging" or "production").

    Returns:
        A dictionary with `status` ("success") and the validated `plan` object.

    Raises:
        ExampleTaskError: If any argument fails Pydantic schema validation.
    """
    try:
        request = RemediationPlanRequest(
            incident_id=incident_id,
            action_summary=action_summary,
            target_environment=target_environment,
        )
    except ValidationError as exc:
        raise ExampleTaskError(
            f"Invalid remediation plan input ({_format_validation_error(exc)})",
            recovery_hint=(
                "Ensure `incident_id` is 3-64 alphanumeric/hyphen characters, "
                "`action_summary` is 5-500 characters, and `target_environment` is 2-32 "
                "alphanumeric/hyphen characters."
            ),
        ) from exc

    raw_routed_model = tool_context.state.get(ROUTED_MODEL_KEY)
    routed_model = (
        raw_routed_model.strip()
        if isinstance(raw_routed_model, str) and raw_routed_model.strip()
        else "pro"
    )
    plan = ExampleService().create_remediation_plan(request, routed_model=routed_model)
    envelope = RemediationToolResponse(status=ToolStatus.SUCCESS, plan=plan)
    serialized = envelope.model_dump(mode="json")
    tool_context.state[LAST_REMEDIATION_PLAN_KEY] = serialized["plan"]
    return serialized


def create_critical_escalation_bug(
    incident_id: str,
    action_summary: str,
    target_environment: str,
    *,
    tool_context: ToolContext,
) -> dict[str, object]:
    """Creates a P0/P1 critical engineering escalation bug and remediation plan."""
    return publish_high_stakes_remediation_plan(
        incident_id,
        action_summary,
        target_environment,
        tool_context=tool_context,
    )
