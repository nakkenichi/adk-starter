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

"""Tests for the process-wide ADK session and artifact service selection."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from google.adk.artifacts import GcsArtifactService, InMemoryArtifactService
from google.adk.memory.in_memory_memory_service import InMemoryMemoryService
from google.adk.sessions.in_memory_session_service import InMemorySessionService

from app.app_utils import services


@pytest.fixture(autouse=True)
def clear_service_caches() -> Iterator[None]:
    """Resets the functools caches so each test selects services from its own env."""
    services.get_session_service.cache_clear()
    services.get_memory_service.cache_clear()
    services.get_artifact_service.cache_clear()
    yield
    services.get_session_service.cache_clear()
    services.get_memory_service.cache_clear()
    services.get_artifact_service.cache_clear()


def test_defaults_to_in_memory_services() -> None:
    assert isinstance(services.get_session_service(), InMemorySessionService)
    assert isinstance(services.get_memory_service(), InMemoryMemoryService)
    assert isinstance(services.get_artifact_service(), InMemoryArtifactService)


def test_services_are_process_wide_singletons() -> None:
    assert services.get_session_service() is services.get_session_service()
    assert services.get_memory_service() is services.get_memory_service()
    assert services.get_artifact_service() is services.get_artifact_service()


def test_logs_bucket_selects_gcs_artifact_service(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOGS_BUCKET_NAME", "demo-logs-bucket")

    assert isinstance(services.get_artifact_service(), GcsArtifactService)


def test_session_service_uri_takes_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SESSION_SERVICE_URI", "memory://")
    monkeypatch.setenv("GOOGLE_CLOUD_AGENT_ENGINE_ID", "123")

    assert isinstance(services.get_session_service(), InMemorySessionService)
