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

"""Pro-tier planner agent factory."""

from __future__ import annotations

from google.adk.agents import LlmAgent

from app.agents.constants import PRO_MODEL
from app.agents.llm import (
    build_gemini,
    default_generate_content_config,
    make_model_router_callback,
)
from app.agents.planner.prompts import PLANNER_DESCRIPTION, PLANNER_INSTRUCTION
from app.models.enums import AgentName
from app.tools.example_tools import (
    create_critical_escalation_bug,
    publish_high_stakes_remediation_plan,
)


def create_planner_agent(*, model: str = PRO_MODEL) -> LlmAgent:
    """Builds the Pro-tier planner agent for Tier-3 escalations and remediation synthesis.

    Args:
        model: Model identifier (defaults to `PRO_MODEL`).

    Returns:
        Configured planner `LlmAgent`.
    """
    return LlmAgent(
        name=AgentName.PLANNER.value,
        model=build_gemini(model),
        description=PLANNER_DESCRIPTION,
        instruction=PLANNER_INSTRUCTION,
        generate_content_config=default_generate_content_config(),
        before_model_callback=make_model_router_callback(model),
        tools=[
            create_critical_escalation_bug,
            publish_high_stakes_remediation_plan,
        ],
    )
