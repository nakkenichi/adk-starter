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

"""Tests for the orchestrator, sequential workflow, planner, and strategic model routing."""

from __future__ import annotations

import pytest
from google.adk.agents import LlmAgent, SequentialAgent
from google.adk.models import Gemini

from app.agents.constants import DEFAULT_MODEL, FAST_MODEL, PRO_MODEL
from app.agents.llm import select_model_for_complexity
from app.agents.orchestrator import (
    create_orchestrator_agent,
    create_sequential_workflow_agent,
)
from app.agents.orchestrator.prompts import ORCHESTRATOR_INSTRUCTION
from app.agents.planner import create_planner_agent
from app.models.enums import AgentName, TaskComplexity


@pytest.mark.parametrize("model", [DEFAULT_MODEL, "gemini-custom-model"])
def test_model_is_shared_with_sub_agents(model: str) -> None:
    agent = create_orchestrator_agent(model=model)

    assert agent.name == AgentName.ORCHESTRATOR.value
    assert isinstance(agent.model, Gemini)
    assert agent.model.model == model
    [specialist, planner] = agent.sub_agents
    assert isinstance(specialist, LlmAgent)
    assert specialist.name == AgentName.SPECIALIST.value
    assert isinstance(specialist.model, Gemini)
    assert specialist.model.model == model
    assert isinstance(planner, LlmAgent)
    assert planner.name == AgentName.PLANNER.value
    assert isinstance(planner.model, Gemini)
    assert planner.model.model == model


def test_default_model_is_used_when_not_specified() -> None:
    agent = create_orchestrator_agent()

    assert isinstance(agent.model, Gemini)
    assert agent.model.model == DEFAULT_MODEL
    [specialist, planner] = agent.sub_agents
    assert isinstance(specialist, LlmAgent)
    assert isinstance(specialist.model, Gemini)
    assert specialist.model.model == FAST_MODEL
    assert isinstance(planner, LlmAgent)
    assert isinstance(planner.model, Gemini)
    assert planner.model.model == PRO_MODEL


def test_instruction_names_the_specialist_and_constitution() -> None:
    assert AgentName.SPECIALIST.value in ORCHESTRATOR_INSTRUCTION
    assert AgentName.PLANNER.value in ORCHESTRATOR_INSTRUCTION
    assert "<constitution>" in ORCHESTRATOR_INSTRUCTION


@pytest.mark.parametrize(
    ("complexity", "expected_model"),
    [
        pytest.param(TaskComplexity.ROUTINE, FAST_MODEL, id="routine_uses_flash"),
        pytest.param(TaskComplexity.COMPLEX, PRO_MODEL, id="complex_uses_pro"),
    ],
)
def test_select_model_for_complexity_routes_strategically(
    complexity: TaskComplexity,
    expected_model: str,
) -> None:
    assert select_model_for_complexity(complexity) == expected_model


def test_sequential_workflow_combines_flash_specialist_and_pro_planner() -> None:
    workflow = create_sequential_workflow_agent()

    assert isinstance(workflow, SequentialAgent)
    assert workflow.name == AgentName.SEQUENTIAL_WORKFLOW.value
    [stage_one, stage_two] = workflow.sub_agents
    assert isinstance(stage_one, LlmAgent)
    assert isinstance(stage_two, LlmAgent)
    assert stage_one.name == AgentName.SPECIALIST.value
    assert stage_two.name == AgentName.PLANNER.value
    assert isinstance(stage_one.model, Gemini)
    assert isinstance(stage_two.model, Gemini)
    assert stage_one.model.model == FAST_MODEL
    assert stage_two.model.model == PRO_MODEL


def test_planner_agent_defaults_to_pro_model() -> None:
    planner = create_planner_agent()

    assert planner.name == AgentName.PLANNER.value
    assert isinstance(planner.model, Gemini)
    assert planner.model.model == PRO_MODEL
