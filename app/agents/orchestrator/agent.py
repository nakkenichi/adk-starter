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

"""Root orchestrator (Coordinator) and deterministic SequentialAgent workflow factories."""

from __future__ import annotations

from google.adk.agents import LlmAgent, SequentialAgent

from app.agents.llm import (
    build_gemini,
    default_generate_content_config,
    make_model_router_callback,
    select_model_for_complexity,
)
from app.agents.orchestrator.prompts import (
    ORCHESTRATOR_DESCRIPTION,
    ORCHESTRATOR_INSTRUCTION,
    SEQUENTIAL_WORKFLOW_DESCRIPTION,
)
from app.agents.planner import create_planner_agent
from app.agents.specialist import create_specialist_agent
from app.models.enums import AgentName, TaskComplexity


def create_orchestrator_agent(
    *,
    model: str | None = None,
    planner_model: str | None = None,
) -> LlmAgent:
    """Builds the root coordinator agent that routes across Flash specialist and Pro planner.

    When `model` is omitted, routine triage and `specialist_agent` are routed via
    `select_model_for_complexity(TaskComplexity.ROUTINE)` (`FAST_MODEL`), while `planner_agent`
    is routed via `select_model_for_complexity(TaskComplexity.COMPLEX)` (`PRO_MODEL`).

    Args:
        model: Optional model override applied to the orchestrator and specialist (and planner
            when `planner_model` is not set).
        planner_model: Optional explicit model override for the Pro-tier `planner_agent`.

    Returns:
        The configured root `LlmAgent`.
    """
    resolved_fast = select_model_for_complexity(TaskComplexity.ROUTINE) if model is None else model
    resolved_pro = (
        planner_model
        if planner_model is not None
        else (model if model is not None else select_model_for_complexity(TaskComplexity.COMPLEX))
    )
    return LlmAgent(
        name=AgentName.ORCHESTRATOR.value,
        model=build_gemini(resolved_fast),
        description=ORCHESTRATOR_DESCRIPTION,
        instruction=ORCHESTRATOR_INSTRUCTION,
        generate_content_config=default_generate_content_config(),
        before_model_callback=make_model_router_callback(resolved_fast),
        sub_agents=[
            create_specialist_agent(model=resolved_fast),
            create_planner_agent(model=resolved_pro),
        ],
    )


def create_sequential_workflow_agent(
    *,
    fast_model: str | None = None,
    pro_model: str | None = None,
) -> SequentialAgent:
    """Builds a deterministic two-stage `SequentialAgent` routing Flash -> Pro.

    Stage 1 runs `specialist_agent` on `fast_model` (Flash) for rapid diagnostic analysis;
    Stage 2 runs `planner_agent` on `pro_model` (Pro) for remediation planning.

    Args:
        fast_model: Model identifier for the fast diagnostic stage.
        pro_model: Model identifier for the reasoning-heavy planning stage.

    Returns:
        Configured `SequentialAgent` pipeline.
    """
    resolved_fast = (
        select_model_for_complexity(TaskComplexity.ROUTINE) if fast_model is None else fast_model
    )
    resolved_pro = (
        select_model_for_complexity(TaskComplexity.COMPLEX) if pro_model is None else pro_model
    )
    return SequentialAgent(
        name=AgentName.SEQUENTIAL_WORKFLOW.value,
        description=SEQUENTIAL_WORKFLOW_DESCRIPTION,
        sub_agents=[
            create_specialist_agent(model=resolved_fast),
            create_planner_agent(model=resolved_pro),
        ],
    )
