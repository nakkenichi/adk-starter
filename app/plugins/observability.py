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

"""ADK observability plugin capturing Intent vs. Outcome logs and OpenTelemetry spans."""

from __future__ import annotations

import uuid

from google.adk.plugins.base_plugin import BasePlugin
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext

from app.agents.state import ACTIVE_TOOL_CORRELATIONS_KEY, INTENT_OUTCOME_TRACE_KEY
from app.models.enums import ToolStatus
from app.services.observability import StructuredAuditLogger


class ObservabilityPlugin(BasePlugin):
    """Captures pre-execution tool intent and post-execution outcome with OTel trace context."""

    def __init__(
        self,
        *,
        name: str = "observability_plugin",
        audit_logger: StructuredAuditLogger | None = None,
    ) -> None:
        """Initializes the observability plugin.

        Args:
            name: Unique plugin name registered with the ADK App.
            audit_logger: Structured JSON logger used to emit intent and outcome events.
        """
        super().__init__(name=name)
        self._audit_logger: StructuredAuditLogger = audit_logger or StructuredAuditLogger()

    @property
    def audit_logger(self) -> StructuredAuditLogger:
        """Returns the underlying `StructuredAuditLogger`."""
        return self._audit_logger

    @staticmethod
    def _correlation_key(tool: BaseTool, tool_context: ToolContext) -> str:
        call_id = getattr(tool_context, "function_call_id", None)
        if isinstance(call_id, str) and call_id.strip():
            return f"{tool.name}:{call_id.strip()}"
        return tool.name

    @classmethod
    def _pop_correlation_id(cls, tool: BaseTool, tool_context: ToolContext) -> str:
        existing_raw = tool_context.state.get(ACTIVE_TOOL_CORRELATIONS_KEY, {})
        active_map: dict[str, str] = (
            {str(k): str(v) for k, v in existing_raw.items() if isinstance(v, str) and v}
            if isinstance(existing_raw, dict)
            else {}
        )
        key = cls._correlation_key(tool, tool_context)
        correlation_id = (
            active_map.pop(key, None) or active_map.pop(tool.name, None) or uuid.uuid4().hex[:12]
        )
        if active_map.get(tool.name) == correlation_id:
            active_map.pop(tool.name, None)
        tool_context.state[ACTIVE_TOOL_CORRELATIONS_KEY] = active_map
        return correlation_id

    async def before_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, object],
        tool_context: ToolContext,
    ) -> dict[str, object] | None:
        """Records the agent's intended action (`agent_intent`) before tool execution."""
        correlation_id = uuid.uuid4().hex[:12]
        existing_raw = tool_context.state.get(ACTIVE_TOOL_CORRELATIONS_KEY, {})
        active_map: dict[str, str] = (
            {str(k): str(v) for k, v in existing_raw.items() if isinstance(v, str) and v}
            if isinstance(existing_raw, dict)
            else {}
        )
        active_map[self._correlation_key(tool, tool_context)] = correlation_id
        active_map[tool.name] = correlation_id
        tool_context.state[ACTIVE_TOOL_CORRELATIONS_KEY] = active_map
        with self._audit_logger.start_span(
            f"tool.intent.{tool.name}",
            attributes={"tool.name": tool.name, "correlation.id": correlation_id},
        ):
            entry = self._audit_logger.record_intent(
                correlation_id=correlation_id,
                tool_name=tool.name,
                tool_args=dict(tool_args),
            )
        self._append_trace_state(tool_context, entry.model_dump(mode="json"))
        return None

    async def after_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, object],
        tool_context: ToolContext,
        result: dict[str, object] | None,
    ) -> dict[str, object] | None:
        """Records the actual outcome (`agent_outcome`) after tool execution."""
        del tool_args
        resolved_result: dict[str, object] = dict(result) if isinstance(result, dict) else {}
        correlation_id = self._pop_correlation_id(tool, tool_context)
        raw_status = str(resolved_result.get("status", ToolStatus.SUCCESS.value))
        status = (
            ToolStatus(raw_status)
            if raw_status in ToolStatus._value2member_map_
            else ToolStatus.SUCCESS
        )
        with self._audit_logger.start_span(
            f"tool.outcome.{tool.name}",
            attributes={
                "tool.name": tool.name,
                "tool.status": status.value,
                "correlation.id": correlation_id,
            },
        ):
            entry = self._audit_logger.record_outcome(
                correlation_id=correlation_id,
                tool_name=tool.name,
                status=status,
                result_summary=resolved_result,
            )
        self._append_trace_state(tool_context, entry.model_dump(mode="json"))
        return None

    async def on_tool_error_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, object],
        tool_context: ToolContext,
        error: Exception,
    ) -> dict[str, object] | None:
        """Records an error outcome (`ToolStatus.ERROR`) when a tool raises an exception."""
        del tool_args
        correlation_id = self._pop_correlation_id(tool, tool_context)
        error_summary: dict[str, object] = {
            "status": ToolStatus.ERROR.value,
            "error_type": type(error).__name__,
            "message": str(error),
        }
        with self._audit_logger.start_span(
            f"tool.outcome.{tool.name}",
            attributes={
                "tool.name": tool.name,
                "tool.status": ToolStatus.ERROR.value,
                "correlation.id": correlation_id,
            },
        ):
            entry = self._audit_logger.record_outcome(
                correlation_id=correlation_id,
                tool_name=tool.name,
                status=ToolStatus.ERROR,
                result_summary=error_summary,
            )
        self._append_trace_state(tool_context, entry.model_dump(mode="json"))
        return None

    def _append_trace_state(
        self,
        tool_context: ToolContext,
        serialized_entry: dict[str, object],
    ) -> None:
        existing_raw = tool_context.state.get(INTENT_OUTCOME_TRACE_KEY, [])
        trace_list: list[dict[str, object]] = (
            [dict(item) for item in existing_raw if isinstance(item, dict)]
            if isinstance(existing_raw, list)
            else []
        )
        trace_list.append(serialized_entry)
        tool_context.state[INTENT_OUTCOME_TRACE_KEY] = trace_list
