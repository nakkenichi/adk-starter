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

"""In-process tests for the FastAPI serving surface (no network: ASGI transport only)."""

from __future__ import annotations

import importlib
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.constants import APP_NAME, PROJECT_NAME

_AGENT_CARD_PATH = f"/a2a/{APP_NAME}/.well-known/agent-card.json"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Starts the app (including its lifespan) without reading a developer `.env` file."""
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: False)
    fast_api_app = importlib.import_module("app.fast_api_app")
    with TestClient(fast_api_app.app) as test_client:
        yield test_client


def test_app_metadata(client: TestClient) -> None:
    assert client.app.title == PROJECT_NAME  # type: ignore[attr-defined]  # ASGI app is FastAPI


def test_agent_card_is_served(client: TestClient) -> None:
    response = client.get(_AGENT_CARD_PATH)

    assert response.status_code == 200
    card = response.json()
    assert card["name"] == "orchestrator_agent"
    assert card["capabilities"]["streaming"] is True


def test_adk_lists_only_the_app(client: TestClient) -> None:
    response = client.get("/list-apps")

    assert response.status_code == 200
    assert response.json() == [APP_NAME]


@pytest.mark.parametrize(
    ("path", "body", "expected_status"),
    [
        ("/api/reasoning_engine", [], 400),
        ("/api/stream_reasoning_engine", "not-an-object", 400),
    ],
)
def test_reasoning_engine_routes_reject_non_object_bodies(
    client: TestClient, path: str, body: object, expected_status: int
) -> None:
    response = client.post(path, json=body)

    assert response.status_code == expected_status


@pytest.mark.parametrize("path", ["/api/reasoning_engine", "/api/stream_reasoning_engine"])
def test_reasoning_engine_routes_reject_unknown_methods(client: TestClient, path: str) -> None:
    response = client.post(path, json={"class_method": "not_an_operation"})

    assert response.status_code == 404


def test_reasoning_engine_query_creates_session(client: TestClient) -> None:
    response = client.post(
        "/api/reasoning_engine",
        json={"class_method": "async_create_session", "input": {"user_id": "test-user"}},
    )

    assert response.status_code == 200
    assert response.json()["output"]["user_id"] == "test-user"


def test_reasoning_engine_stream_query_runs_agent_offline(client: TestClient) -> None:
    response = client.post(
        "/api/stream_reasoning_engine",
        json={
            "class_method": "async_stream_query",
            "input": {"user_id": "test-user", "message": "What can you do?"},
        },
    )

    assert response.status_code == 200
    assert "Mocked offline response" in response.text
