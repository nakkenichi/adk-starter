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

"""Specialist agent factory: owns the domain diagnostic, memory, and remediation tools."""

from __future__ import annotations

from google.adk.agents import LlmAgent

from app.agents.constants import DEFAULT_MODEL
from app.agents.llm import (
    build_gemini,
    default_generate_content_config,
    make_model_router_callback,
)
from app.agents.specialist.prompts import SPECIALIST_DESCRIPTION, SPECIALIST_INSTRUCTION
from app.models.enums import AgentName
from app.tools.example_tools import (
    diagnose_support_case_symptom,
    retrieve_customer_support_memories,
)


def create_specialist_agent(*, model: str = DEFAULT_MODEL) -> LlmAgent:
    """Builds the Tier-1/Tier-2 support specialist agent that runs diagnostic and memory tools.

    Args:
        model: Model identifier (defaults to `DEFAULT_MODEL` / Flash tier).

    Returns:
        The configured specialist `LlmAgent`.
    """
    return LlmAgent(
        name=AgentName.SPECIALIST.value,
        model=build_gemini(model),
        description=SPECIALIST_DESCRIPTION,
        instruction=SPECIALIST_INSTRUCTION,
        generate_content_config=default_generate_content_config(),
        before_model_callback=make_model_router_callback(model),
        tools=[
            diagnose_support_case_symptom,
            retrieve_customer_support_memories,
        ],
    )
