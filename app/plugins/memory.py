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

"""ADK plugin for non-blocking background memory consolidation.

History compaction is handled natively by ADK `EventsCompactionConfig` in `app/agent.py`.
This plugin schedules asynchronous PII-scrubbed memory consolidation at the end of a turn
without mutating `llm_request.contents` or blocking the event loop.
"""

from __future__ import annotations

from google.adk.agents.base_agent import BaseAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin
from google.genai import types

from app.agents.state import (
    GUARDRAIL_BLOCKED_KEY,
    LAST_CONSOLIDATED_EVENT_ID_KEY,
    OBSERVATION_LOG_KEY,
)
from app.plugins.guards import is_prompt_injection
from app.services.memory_service import (
    PersistentMemoryService,
    get_shared_memory_service,
)


class AsyncMemoryConsolidationPlugin(BasePlugin):
    """Schedules non-blocking background memory consolidation at the end of an agent turn."""

    def __init__(
        self,
        *,
        name: str = "async_memory_consolidation",
        max_window_tokens: int = 4000,
        keep_recent_turns: int = 4,
        memory_service: PersistentMemoryService | None = None,
    ) -> None:
        """Initializes the memory consolidation plugin.

        Args:
            name: Unique plugin name registered with the ADK App.
            max_window_tokens: Retained for constructor backward compatibility; native
                `EventsCompactionConfig` owns history compaction.
            keep_recent_turns: Retained for constructor backward compatibility.
            memory_service: Underlying persistent memory service instance.
        """
        super().__init__(name=name)
        del max_window_tokens, keep_recent_turns
        self._memory_service: PersistentMemoryService = (
            memory_service or get_shared_memory_service()
        )

    @property
    def memory_service(self) -> PersistentMemoryService:
        """Returns the underlying `PersistentMemoryService`."""
        return self._memory_service

    def _consolidate_latest_user_turn(self, callback_context: CallbackContext) -> None:
        """Consolidates the latest user utterance once per event ID without duplicating."""
        if callback_context.state.get(GUARDRAIL_BLOCKED_KEY):
            return

        session = callback_context.session
        events = getattr(session, "events", None)
        if not isinstance(events, list):
            return

        latest_text = ""
        latest_event_key = ""
        for idx, event in enumerate(events):
            if event.author == "user" and event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text and part.text.strip():
                        latest_text = part.text.strip()
                        event_id = getattr(event, "id", None)
                        latest_event_key = (
                            str(event_id) if event_id else f"{session.id}:{idx}:{latest_text[:64]}"
                        )

        if not latest_text or is_prompt_injection(latest_text):
            return

        if callback_context.state.get(LAST_CONSOLIDATED_EVENT_ID_KEY) == latest_event_key:
            return
        callback_context.state[LAST_CONSOLIDATED_EVENT_ID_KEY] = latest_event_key

        record = self._memory_service.build_scrubbed_record(
            session.user_id,
            session.id,
            latest_text,
        )
        existing_raw = callback_context.state.get(OBSERVATION_LOG_KEY, [])
        existing_list: list[dict[str, object]] = (
            [dict(item) for item in existing_raw if isinstance(item, dict)]
            if isinstance(existing_raw, list)
            else []
        )
        existing_list.append(record.model_dump(mode="json"))
        callback_context.state[OBSERVATION_LOG_KEY] = existing_list
        self._memory_service.schedule_background_consolidation(
            session.user_id,
            session.id,
            (latest_text,),
        )

    async def before_model_callback(
        self,
        *,
        callback_context: CallbackContext,
        llm_request: LlmRequest,
    ) -> LlmResponse | None:
        """Consolidates the user utterance onto the model event delta; ADK compacts history."""
        del llm_request
        self._consolidate_latest_user_turn(callback_context)
        return None

    async def after_agent_callback(
        self,
        *,
        agent: BaseAgent,
        callback_context: CallbackContext,
    ) -> types.Content | None:
        """Schedules async background memory consolidation if not already recorded in the turn."""
        del agent
        self._consolidate_latest_user_turn(callback_context)
        return None

    async def drain(self) -> None:
        """Waits for all background memory consolidation tasks to finish."""
        await self._memory_service.drain()
