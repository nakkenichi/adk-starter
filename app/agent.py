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

"""ADK application entry point: root agent, native event compaction, and app-wide plugins."""

from __future__ import annotations

from typing import Final

from google.adk.agents import LlmAgent
from google.adk.apps import App
from google.adk.apps.app import EventsCompactionConfig

from app.agents.constants import (
    COMPACTION_EVENT_RETENTION_SIZE,
    COMPACTION_INTERVAL,
    COMPACTION_OVERLAP_SIZE,
    COMPACTION_TOKEN_THRESHOLD,
)
from app.agents.orchestrator import create_orchestrator_agent
from app.constants import APP_NAME
from app.plugins.guards import DomainErrorGuardPlugin
from app.plugins.memory import AsyncMemoryConsolidationPlugin
from app.plugins.observability import ObservabilityPlugin

root_agent: Final[LlmAgent] = create_orchestrator_agent()

compaction_config: Final[EventsCompactionConfig] = EventsCompactionConfig(
    compaction_interval=COMPACTION_INTERVAL,
    overlap_size=COMPACTION_OVERLAP_SIZE,
    token_threshold=COMPACTION_TOKEN_THRESHOLD,
    event_retention_size=COMPACTION_EVENT_RETENTION_SIZE,
)

app: Final[App] = App(
    name=APP_NAME,
    root_agent=root_agent,
    events_compaction_config=compaction_config,
    plugins=[
        ObservabilityPlugin(),
        DomainErrorGuardPlugin(),
        AsyncMemoryConsolidationPlugin(),
    ],
)
