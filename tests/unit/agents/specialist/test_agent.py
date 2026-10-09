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

"""Tests for the specialist agent factory."""

from __future__ import annotations

from google.adk.models import Gemini

from app.agents.constants import MODEL_RETRY_ATTEMPTS
from app.agents.specialist import create_specialist_agent
from app.agents.specialist.prompts import SPECIALIST_INSTRUCTION
from app.models.enums import AgentName
from app.tools.example_tools import (
    diagnose_support_case_symptom,
    retrieve_customer_support_memories,
    retrieve_persistent_memories,
    run_example_task,
)


def test_specialist_owns_the_domain_tools() -> None:
    agent = create_specialist_agent(model="gemini-custom-model")

    assert agent.name == AgentName.SPECIALIST.value
    assert agent.tools == [
        diagnose_support_case_symptom,
        retrieve_customer_support_memories,
    ]
    assert agent.sub_agents == []


def test_model_uses_project_retry_policy() -> None:
    agent = create_specialist_agent(model="gemini-custom-model")

    assert isinstance(agent.model, Gemini)
    assert agent.model.model == "gemini-custom-model"
    assert agent.model.retry_options is not None
    assert agent.model.retry_options.attempts == MODEL_RETRY_ATTEMPTS


def test_instruction_names_the_tools_and_constitution() -> None:
    assert "<constitution>" in SPECIALIST_INSTRUCTION
    assert diagnose_support_case_symptom.__name__ in SPECIALIST_INSTRUCTION
    assert retrieve_customer_support_memories.__name__ in SPECIALIST_INSTRUCTION
    assert run_example_task.__name__ not in SPECIALIST_INSTRUCTION
    assert retrieve_persistent_memories.__name__ not in SPECIALIST_INSTRUCTION
