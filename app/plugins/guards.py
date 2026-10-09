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

"""App-wide ADK plugins enforcing guardrails, HITL confirmation gates, and guided error recovery."""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from typing import Final

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types

from app.agents.state import (
    GUARDRAIL_BLOCKED_KEY,
    HITL_APPROVED_ACTIONS_KEY,
    PENDING_HITL_ACTION_KEY,
    SELF_EVAL_STATUS_KEY,
)
from app.models.enums import ToolStatus
from app.models.exceptions import AgentDomainError
from app.services.redaction import PiiRedactionService

DEFAULT_HIGH_STAKES_TOOLS: Final[frozenset[str]] = frozenset(
    {
        "create_critical_escalation_bug",
        "publish_high_stakes_remediation_plan",
    }
)

_PROMPT_INJECTION_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:ignore\s+all\s+previous\s+instructions|disregard\s+system\s+prompt|"
    r"reveal\s+your\s+system\s+instructions)",
    re.IGNORECASE,
)

_HITL_CONFIRMATION_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:approve|approved|confirm|confirmed|yes,\s*proceed)\b",
    re.IGNORECASE,
)

_HITL_NEGATION_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:not|never|no|don't|do\s+not|cannot|can't|decline|reject|cancel)\b",
    re.IGNORECASE,
)

_UNGROUNDED_EXECUTION_CLAIM_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:has\s+been\s+published|successfully\s+published|plan\s+is\s+now\s+published|"
    r"executed\s+the\s+remediation\s+plan|bug\s+has\s+been\s+(?:created|filed)|"
    r"successfully\s+(?:created|filed)\s+(?:the\s+)?(?:critical\s+)?escalation\s+bug|"
    r"created\s+the\s+critical\s+escalation\s+bug)\b",
    re.IGNORECASE,
)

_logger: Final[logging.Logger] = logging.getLogger(__name__)


def is_prompt_injection(text: str) -> bool:
    """Returns True when `text` matches a known prompt-injection pattern."""
    return bool(text and _PROMPT_INJECTION_RE.search(text))


def _is_affirmative_confirmation(text: str) -> bool:
    """Returns True when `text` affirmatively confirms an action without negation or questions."""
    stripped = text.strip()
    if not stripped or "?" in stripped:
        return False
    if _HITL_NEGATION_RE.search(stripped):
        return False
    return bool(_HITL_CONFIRMATION_RE.search(stripped))


class DomainErrorGuardPlugin(BasePlugin):
    """Enforces policy guardrails, HITL approval gates, self-eval, and guided error recovery.

    1. **Policy Guardrail (`before_model_callback`)**: Blocks prompt-injection attempts before
       they reach the model, and records human approval when the user confirms a pending
       high-stakes tool action.
    2. **Self-Evaluation Guardrail (`after_model_callback`)**: Verifies model outputs do not leak
       inline credentials/PII or falsely claim a paused HITL action already executed, recording
       self-eval status in `session.state`.
    3. **Human-in-the-Loop Hook (`before_tool_callback`)**: Halts high-stakes tools unless human
       approval is recorded in `session.state[HITL_APPROVED_ACTIONS_KEY]`.
    4. **Guided Error Handling (`on_tool_error_callback`)**: Converts `AgentDomainError` into
       structured tool responses carrying `recovery_hint` instructions.
    """

    def __init__(
        self,
        *,
        name: str = "domain_error_guard",
        high_stakes_tools: Sequence[str] = tuple(DEFAULT_HIGH_STAKES_TOOLS),
    ) -> None:
        """Initializes the guard plugin.

        Args:
            name: Unique plugin name registered with the ADK App.
            high_stakes_tools: Tool names requiring explicit human confirmation before execution.
        """
        super().__init__(name=name)
        self._high_stakes_tools: frozenset[str] = frozenset(high_stakes_tools)
        self._redactor: PiiRedactionService = PiiRedactionService()

    async def before_model_callback(
        self,
        *,
        callback_context: CallbackContext,
        llm_request: LlmRequest,
    ) -> LlmResponse | None:
        """Blocks prompt-injection attempts and records human approval of pending HITL tools."""
        latest_user_text = self._extract_latest_user_text(llm_request)
        if is_prompt_injection(latest_user_text):
            callback_context.state[GUARDRAIL_BLOCKED_KEY] = True
            _logger.warning("Blocked prompt-injection attempt in before_model_callback.")
            return LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part.from_text(
                            text=(
                                "Request declined by security guardrail: instructions "
                                "attempting to override system policy are not permitted."
                            )
                        )
                    ],
                )
            )

        pending = callback_context.state.get(PENDING_HITL_ACTION_KEY)
        if isinstance(pending, dict) and pending.get("tool"):
            final_user_text = self._extract_final_turn_user_text(llm_request)
            if final_user_text and _is_affirmative_confirmation(final_user_text):
                tool_name = str(pending["tool"])
                pending_args_raw = pending.get("tool_args")
                approval_entry: dict[str, object] = {
                    "tool": tool_name,
                    "tool_args": (
                        dict(pending_args_raw) if isinstance(pending_args_raw, dict) else {}
                    ),
                }
                approved_raw = callback_context.state.get(HITL_APPROVED_ACTIONS_KEY, [])
                approved_list: list[object] = (
                    list(approved_raw) if isinstance(approved_raw, list) else []
                )
                if approval_entry not in approved_list:
                    approved_list.append(approval_entry)
                callback_context.state[HITL_APPROVED_ACTIONS_KEY] = approved_list
                callback_context.state[PENDING_HITL_ACTION_KEY] = None
                _logger.info("Recorded human approval for pending high-stakes tool %s.", tool_name)
        return None

    @staticmethod
    def _extract_latest_user_text(llm_request: LlmRequest) -> str:
        for content in reversed(llm_request.contents or []):
            if content.role == "user" and content.parts:
                texts = [part.text for part in content.parts if part.text]
                if texts:
                    return " ".join(texts)
        return ""

    @staticmethod
    def _extract_final_turn_user_text(llm_request: LlmRequest) -> str:
        """Returns user text only when the final content item in `llm_request` is user text."""
        contents = llm_request.contents or []
        if not contents:
            return ""
        last = contents[-1]
        if last.role != "user" or not last.parts:
            return ""
        if any(getattr(part, "function_response", None) is not None for part in last.parts):
            return ""
        texts = [part.text for part in last.parts if part.text]
        return " ".join(texts) if texts else ""

    async def after_model_callback(
        self,
        *,
        callback_context: CallbackContext,
        llm_response: LlmResponse,
    ) -> LlmResponse | None:
        """Runs a post-generation self-evaluation check for PII and ungrounded HITL claims."""
        if (
            not llm_response.content
            or not llm_response.content.parts
            or not any(part.text for part in llm_response.content.parts)
        ):
            callback_context.state[SELF_EVAL_STATUS_KEY] = "empty_or_tool_call"
            return None

        pending = callback_context.state.get(PENDING_HITL_ACTION_KEY)
        has_pending_hitl = isinstance(pending, dict) and bool(pending.get("tool"))

        redacted_any = False
        ungrounded_hitl_claim = False
        new_parts: list[types.Part] = []
        for part in llm_response.content.parts:
            if part.text:
                text_value = part.text
                if has_pending_hitl and _UNGROUNDED_EXECUTION_CLAIM_RE.search(text_value):
                    ungrounded_hitl_claim = True
                    pending_tool = str(pending.get("tool")) if isinstance(pending, dict) else ""
                    text_value = (
                        f"Action '{pending_tool}' is currently paused awaiting explicit human "
                        "confirmation and has not been executed yet."
                    )
                scrubbed, count = self._redactor.redact_text(text_value)
                if count > 0:
                    redacted_any = True
                    new_parts.append(types.Part.from_text(text=scrubbed))
                elif ungrounded_hitl_claim:
                    new_parts.append(types.Part.from_text(text=text_value))
                else:
                    new_parts.append(part)
            else:
                new_parts.append(part)

        if ungrounded_hitl_claim or redacted_any:
            callback_context.state[SELF_EVAL_STATUS_KEY] = (
                "ungrounded_hitl_claim_corrected" if ungrounded_hitl_claim else "redacted_pii"
            )
            return llm_response.model_copy(
                update={
                    "content": types.Content(role=llm_response.content.role, parts=new_parts),
                }
            )

        callback_context.state[SELF_EVAL_STATUS_KEY] = "passed"
        return None

    async def before_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, object],
        tool_context: ToolContext,
    ) -> dict[str, object] | None:
        """Enforces a Human-in-the-Loop confirmation stop for high-stakes tools.

        Args:
            tool: Tool about to run.
            tool_args: Arguments the model supplied for the tool.
            tool_context: Invocation context, including session state.

        Returns:
            None to let the tool run, or an `awaiting_confirmation` payload pausing execution.
        """
        if tool.name not in self._high_stakes_tools:
            return None

        state = tool_context.state
        approved_raw = state.get(HITL_APPROVED_ACTIONS_KEY, [])
        approved_list: list[object] = list(approved_raw) if isinstance(approved_raw, list) else []
        pending_existing = state.get(PENDING_HITL_ACTION_KEY)
        matched_approval = False
        remaining_approvals: list[object] = []
        for item in approved_list:
            if isinstance(item, dict) and item.get("tool") == tool.name:
                item_args = item.get("tool_args")
                if not matched_approval and (
                    not isinstance(item_args, dict) or dict(item_args) == dict(tool_args)
                ):
                    matched_approval = True
            elif item == tool.name:
                if not matched_approval and not (
                    isinstance(pending_existing, dict)
                    and pending_existing.get("tool") == tool.name
                    and isinstance(pending_existing.get("tool_args"), dict)
                    and dict(pending_existing["tool_args"]) != dict(tool_args)
                ):
                    matched_approval = True
            else:
                remaining_approvals.append(item)

        if matched_approval or len(remaining_approvals) != len(approved_list):
            state[HITL_APPROVED_ACTIONS_KEY] = remaining_approvals
        if matched_approval:
            state[PENDING_HITL_ACTION_KEY] = None
            return None

        pending_payload: dict[str, object] = {
            "tool": tool.name,
            "tool_args": dict(tool_args),
        }
        state[PENDING_HITL_ACTION_KEY] = pending_payload
        _logger.info("HITL gate paused high-stakes tool %s awaiting human confirmation.", tool.name)
        return {
            "status": ToolStatus.AWAITING_CONFIRMATION.value,
            "tool": tool.name,
            "message": (
                f"High-stakes tool '{tool.name}' requires explicit human confirmation before "
                "execution."
            ),
            "recovery_hint": (
                "Present the proposed parameters to the user and ask for explicit confirmation "
                "to proceed with this action."
            ),
        }

    async def on_tool_error_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, object],
        tool_context: ToolContext,
        error: Exception,
    ) -> dict[str, object] | None:
        """Converts AgentDomainError into a structured error response with recovery guidance.

        Args:
            tool: Tool that raised the error.
            tool_args: Arguments the model supplied for the tool.
            tool_context: Invocation context, including session state.
            error: Exception raised by the tool.

        Returns:
            An error payload with `recovery_hint` for domain errors, else None so bugs propagate.
        """
        del tool_args, tool_context
        if not isinstance(error, AgentDomainError):
            return None
        _logger.warning("Tool %s rejected the request: %s", tool.name, error)
        return {
            "status": ToolStatus.ERROR.value,
            "error_type": type(error).__name__,
            "message": str(error),
            "recovery_hint": error.recovery_hint,
        }
