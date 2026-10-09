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

"""App invariant tests and offline multi-agent flow tests."""

from __future__ import annotations

from unittest.mock import MagicMock

from google.adk.events import Event
from google.adk.runners import InMemoryRunner
from google.genai import types

import app as app_package
from app.agent import app, root_agent
from app.agents.state import LAST_EXAMPLE_RESULT_KEY
from app.constants import APP_NAME
from app.models.enums import AgentName, ToolStatus
from app.plugins.guards import DomainErrorGuardPlugin
from app.tools.example_tools import diagnose_support_case_symptom

_USER_ID = "test-user"


def _function_call(name: str, args: dict[str, object]) -> types.GenerateContentResponse:
    """Builds a model response that calls a single function."""
    part = types.Part(function_call=types.FunctionCall(name=name, args=args))
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(role="model", parts=[part]),
                finish_reason=types.FinishReason.STOP,
            )
        ]
    )


def _text(text: str) -> types.GenerateContentResponse:
    """Builds a plain-text model response."""
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(role="model", parts=[types.Part.from_text(text=text)]),
                finish_reason=types.FinishReason.STOP,
            )
        ]
    )


async def _run(prompt: str) -> tuple[list[Event], dict[str, object]]:
    """Runs the app once with an in-memory runner and returns the events and final state."""
    runner = InMemoryRunner(app=app)
    session = await runner.session_service.create_session(app_name=app.name, user_id=_USER_ID)
    events = [
        event
        async for event in runner.run_async(
            user_id=_USER_ID,
            session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part.from_text(text=prompt)]),
        )
    ]
    final = await runner.session_service.get_session(
        app_name=app.name, user_id=_USER_ID, session_id=session.id
    )
    assert final is not None
    return events, dict(final.state)


def _texts(events: list[Event]) -> list[str]:
    return [
        part.text
        for event in events
        if event.content and event.content.parts
        for part in event.content.parts
        if part.text
    ]


def _tool_responses(events: list[Event], tool_name: str) -> list[dict[str, object]]:
    return [
        dict(response.response or {})
        for event in events
        for response in event.get_function_responses()
        if response.name == tool_name
    ]


def test_app_name_is_app() -> None:
    assert app.name == APP_NAME == "app"


def test_package_exports_app_and_root_agent() -> None:
    assert app_package.app is app
    assert app_package.root_agent is root_agent


def test_root_agent_is_the_orchestrator() -> None:
    assert app.root_agent is root_agent
    assert root_agent.name == AgentName.ORCHESTRATOR.value
    assert [agent.name for agent in root_agent.sub_agents] == [
        AgentName.SPECIALIST.value,
        AgentName.PLANNER.value,
    ]


def test_app_registers_domain_error_guard() -> None:
    assert any(isinstance(plugin, DomainErrorGuardPlugin) for plugin in app.plugins)


async def test_orchestrator_answers_directly_offline() -> None:
    events, state = await _run("What can you do?")

    assert _texts(events)[-1] == "Mocked offline response"
    assert LAST_EXAMPLE_RESULT_KEY not in state


async def test_delegation_runs_tool_and_stores_result(mock_genai_client: MagicMock) -> None:
    mock_genai_client.aio.models.generate_content.side_effect = [
        _function_call("transfer_to_agent", {"agent_name": AgentName.SPECIALIST.value}),
        _function_call(diagnose_support_case_symptom.__name__, {"topic": "quarterly sales"}),
        _text("Here is the analysis."),
    ]

    events, state = await _run("Analyse quarterly sales")

    [tool_response] = _tool_responses(events, diagnose_support_case_symptom.__name__)
    assert tool_response["status"] == ToolStatus.SUCCESS.value
    stored = state[LAST_EXAMPLE_RESULT_KEY]
    assert isinstance(stored, dict)
    assert stored["topic"] == "quarterly sales"
    assert _texts(events)[-1] == "Here is the analysis."


async def test_domain_error_becomes_structured_tool_response(
    mock_genai_client: MagicMock,
) -> None:
    mock_genai_client.aio.models.generate_content.side_effect = [
        _function_call("transfer_to_agent", {"agent_name": AgentName.SPECIALIST.value}),
        _function_call(diagnose_support_case_symptom.__name__, {"topic": "   "}),
        _text("Please tell me which symptom to diagnose."),
    ]

    events, state = await _run("Diagnose")

    [tool_response] = _tool_responses(events, diagnose_support_case_symptom.__name__)
    assert tool_response["status"] == ToolStatus.ERROR.value
    assert tool_response["error_type"] == "ExampleTaskError"
    assert LAST_EXAMPLE_RESULT_KEY not in state
    assert _texts(events)[-1] == "Please tell me which symptom to diagnose."


def test_golden_eval_dataset_is_well_formed_and_covers_support_journeys() -> None:
    import json
    from pathlib import Path

    dataset_path = Path(__file__).resolve().parents[1] / "eval" / "datasets" / "basic-dataset.json"
    payload = json.loads(dataset_path.read_text(encoding="utf-8"))
    eval_cases = payload["eval_cases"]
    assert isinstance(eval_cases, list)
    assert len(eval_cases) >= 1
    case_ids = [str(case.get("eval_case_id", "")).strip() for case in eval_cases]
    assert all(case_ids)
    assert len(set(case_ids)) == len(case_ids)
    for case in eval_cases:
        assert case["prompt"]["role"] == "user"
        assert case["prompt"]["parts"][0]["text"].strip()
        assert case["reference"]["response"]["role"] == "model"
        assert case["reference"]["response"]["parts"][0]["text"].strip()
