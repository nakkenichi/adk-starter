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

"""Tests for the cloud support diagnostic and escalation service."""

from __future__ import annotations

import pytest

from app.models.schema import ExampleRequest
from app.services.example_service import ExampleService


@pytest.mark.parametrize(
    ("topic", "expected_word_count", "expected_runbook", "expected_severity"),
    [
        ("Postgres connection pool deadlock", 4, "RUNBOOK-DB-309", "P1"),
        ("GKE ingress 504 timeout and latency", 6, "RUNBOOK-NET-101", "P2"),
        ("IAM 403 permission denied on secret", 6, "RUNBOOK-IAM-204", "P2"),
        ("429 rate limit quota exceeded", 5, "RUNBOOK-QUOTA-412", "P3"),
        ("  general account inquiry  ", 3, "RUNBOOK-GEN-001", "P3"),
    ],
)
def test_run_returns_structured_runbook_diagnosis(
    topic: str,
    expected_word_count: int,
    expected_runbook: str,
    expected_severity: str,
) -> None:
    request = ExampleRequest(topic=topic)

    result = ExampleService().run(request)

    assert result.topic == request.topic
    assert result.word_count == expected_word_count
    assert result.matched_runbook == expected_runbook
    assert result.severity_tier == expected_severity
    assert len(result.recommended_actions) >= 3
    assert request.topic in result.summary
    assert expected_runbook in result.summary
