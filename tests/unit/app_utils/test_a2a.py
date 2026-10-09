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

"""Tests for A2A agent-card URL resolution."""

from __future__ import annotations

import pytest

from app.app_utils.a2a import resolve_app_url


@pytest.mark.parametrize(
    ("explicit", "env", "expected"),
    [
        (
            "https://explicit.example",
            {"APP_URL": "https://env.example"},
            "https://explicit.example",
        ),
        (None, {"APP_URL": "https://env.example"}, "https://env.example"),
        (
            None,
            {
                "GOOGLE_CLOUD_AGENT_ENGINE_ID": "123",
                "GOOGLE_CLOUD_PROJECT": "demo-project",
                "GOOGLE_CLOUD_AGENT_ENGINE_LOCATION": "us-central1",
            },
            "https://us-central1-aiplatform.googleapis.com/reasoningEngines/v1"
            "/projects/demo-project/locations/us-central1/reasoningEngines/123/api",
        ),
        (
            None,
            {"GOOGLE_CLOUD_AGENT_ENGINE_ID": "123", "GOOGLE_CLOUD_PROJECT": "demo-project"},
            "https://us-central1-aiplatform.googleapis.com/reasoningEngines/v1"
            "/projects/demo-project/locations/us-central1/reasoningEngines/123/api",
        ),
        (None, {}, "http://0.0.0.0:8080"),
    ],
)
def test_resolve_app_url(
    monkeypatch: pytest.MonkeyPatch,
    explicit: str | None,
    env: dict[str, str],
    expected: str,
) -> None:
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    assert resolve_app_url(explicit) == expected


def test_resolve_app_url_requires_project_for_agent_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOOGLE_CLOUD_AGENT_ENGINE_ID", "123")
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT")

    assert resolve_app_url() == "http://0.0.0.0:8080"
