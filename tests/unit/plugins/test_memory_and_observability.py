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

"""Unit tests for AsyncMemoryConsolidationPlugin and ObservabilityPlugin."""

from __future__ import annotations

from unittest.mock import MagicMock

from google.adk.events import Event
from google.adk.models.llm_request import LlmRequest
from google.adk.tools.function_tool import FunctionTool
from google.genai import types

from app.agents.state import (
    ACTIVE_TOOL_CORRELATIONS_KEY,
    INTENT_OUTCOME_TRACE_KEY,
    OBSERVATION_LOG_KEY,
)
from app.models.enums import AuditEventType, ToolStatus
from app.plugins.memory import AsyncMemoryConsolidationPlugin
from app.plugins.observability import ObservabilityPlugin
from app.tools.example_tools import retrieve_persistent_memories, run_example_task

_TOOL = FunctionTool(run_example_task)


async def test_memory_plugin_consolidates_async_and_delegates_compaction_to_adk() -> None:
    plugin = AsyncMemoryConsolidationPlugin()
    callback_context = MagicMock()
    callback_context.state = {}
    callback_context.session.user_id = "user-1"
    callback_context.session.id = "sess-1"
    event = Event(
        author="user",
        content=types.Content(
            role="user",
            parts=[
                types.Part.from_text(
                    text="Remember my escalation contact is ops@example.com in asia-northeast1"
                )
            ],
        ),
    )
    callback_context.session.events = [event]

    await plugin.after_agent_callback(
        agent=MagicMock(),
        callback_context=callback_context,
    )
    # Second invocation with the same final user event (e.g. sub-agent transfer) is deduplicated.
    await plugin.after_agent_callback(
        agent=MagicMock(),
        callback_context=callback_context,
    )
    await plugin.drain()

    stored = callback_context.state[OBSERVATION_LOG_KEY]
    assert isinstance(stored, list)
    assert len(stored) == 1
    assert "[REDACTED_EMAIL]" in stored[0]["fact"]

    tool_ctx = MagicMock()
    tool_ctx.state = dict(callback_context.state)
    tool_ctx.user_id = "user-1"
    retrieved = retrieve_persistent_memories(
        user_id="user-1",
        query="escalation contact asia-northeast1",
        tool_context=tool_ctx,
    )
    assert isinstance(retrieved["memories"], list)
    assert len(retrieved["memories"]) >= 1

    request = LlmRequest(
        contents=[
            types.Content(role="user", parts=[types.Part.from_text(text="old turn one " * 10)]),
            types.Content(role="model", parts=[types.Part.from_text(text="old turn two " * 10)]),
            types.Content(role="user", parts=[types.Part.from_text(text="recent turn three")]),
            types.Content(role="user", parts=[types.Part.from_text(text="recent turn four")]),
        ]
    )
    await plugin.before_model_callback(callback_context=callback_context, llm_request=request)

    assert len(request.contents) == 4


async def test_observability_plugin_records_intent_and_outcome_isolated_by_session() -> None:
    plugin = ObservabilityPlugin()
    session_a = MagicMock()
    session_a.state = {}
    session_a.function_call_id = "call-a1"
    session_b = MagicMock()
    session_b.state = {}
    session_b.function_call_id = "call-b1"

    await plugin.before_tool_callback(
        tool=_TOOL,
        tool_args={"topic": "latency spike for user@example.com"},
        tool_context=session_a,
    )
    await plugin.before_tool_callback(
        tool=_TOOL,
        tool_args={"topic": "concurrent request in session B"},
        tool_context=session_b,
    )
    raw_a = session_a.state[ACTIVE_TOOL_CORRELATIONS_KEY][_TOOL.name]
    raw_b = session_b.state[ACTIVE_TOOL_CORRELATIONS_KEY][_TOOL.name]
    corr_a = raw_a[0] if isinstance(raw_a, list) else raw_a
    corr_b = raw_b[0] if isinstance(raw_b, list) else raw_b
    assert corr_a != corr_b

    await plugin.after_tool_callback(
        tool=_TOOL,
        tool_args={"topic": "latency spike for user@example.com"},
        tool_context=session_a,
        result={"status": ToolStatus.SUCCESS.value},
    )
    await plugin.on_tool_error_callback(
        tool=_TOOL,
        tool_args={"topic": "concurrent request in session B"},
        tool_context=session_b,
        error=RuntimeError("simulated failure"),
    )

    entries = plugin.audit_logger.entries
    assert len(entries) == 4
    assert entries[0].event_type == AuditEventType.AGENT_INTENT
    assert entries[2].event_type == AuditEventType.AGENT_OUTCOME
    assert entries[0].correlation_id == entries[2].correlation_id == corr_a
    assert entries[1].correlation_id == entries[3].correlation_id == corr_b
    assert entries[3].status == ToolStatus.ERROR
    trace_state = session_a.state[INTENT_OUTCOME_TRACE_KEY]
    assert isinstance(trace_state, list)
    assert len(trace_state) == 2
