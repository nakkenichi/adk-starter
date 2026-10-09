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

"""Unit tests for PiiRedactionService (deterministic scrubbing + Cloud DLP protocol)."""

from __future__ import annotations

import pytest

from app.services.redaction import (
    REDACTED_CARD,
    REDACTED_EMAIL,
    REDACTED_PHONE,
    REDACTED_SSN,
    REDACTED_TOKEN,
    REDACTED_USERINFO,
    GoogleCloudDlpClient,
    PiiRedactionService,
)


class _StubDlpClient:
    """Stub Cloud DLP client replacing 'SECRET_NAME' with '[DLP_REDACTED]'."""

    def deidentify_text(self, text: str, *, project_id: str) -> str:
        assert project_id == "demo-project"
        return text.replace("SECRET_NAME", "[DLP_REDACTED]")


@pytest.mark.parametrize(
    ("raw_text", "expected_substring", "min_count"),
    [
        pytest.param(
            "Contact jane.doe@example.com ASAP",
            REDACTED_EMAIL,
            1,
            id="email",
        ),
        pytest.param(
            "postgresql://dbuser:hunter2@db.internal:5432/prod",
            f"postgresql://{REDACTED_USERINFO}db.internal:5432/prod",
            1,
            id="url_credentials",
        ),
        pytest.param(
            "SSN 123-45-6789 on file",
            REDACTED_SSN,
            1,
            id="ssn",
        ),
        pytest.param(
            "Call +1 (415) 555-0199 now",
            REDACTED_PHONE,
            1,
            id="phone",
        ),
        pytest.param(
            "Card 4111-1111-1111-1234",
            REDACTED_CARD,
            1,
            id="credit_card",
        ),
        pytest.param(
            "Header Bearer tok_test_synthetic_value_12345",
            REDACTED_TOKEN,
            1,
            id="bearer_token",
        ),
        pytest.param(
            "Clean operational telemetry message",
            "Clean operational telemetry message",
            0,
            id="clean_text",
        ),
    ],
)
def test_redact_text_scrubs_sensitive_patterns(
    raw_text: str,
    expected_substring: str,
    min_count: int,
) -> None:
    service = PiiRedactionService()

    scrubbed, count = service.redact_text(raw_text)

    assert expected_substring in scrubbed
    assert count >= min_count


def test_redact_text_invokes_optional_dlp_client() -> None:
    service = PiiRedactionService(dlp_client=_StubDlpClient(), project_id="demo-project")

    scrubbed, count = service.redact_text("Customer SECRET_NAME emailed user@example.com")

    assert "[DLP_REDACTED]" in scrubbed
    assert REDACTED_EMAIL in scrubbed
    assert count == 2


def test_redact_mapping_scrubs_nested_structures() -> None:
    service = PiiRedactionService()
    payload: dict[str, object] = {
        "user": "alice@example.com",
        "nested": {"dsn": "redis://admin:pw123@cache:6379/0", "count": 3},
        "items": ["123-45-6789", 42],
    }

    scrubbed, count = service.redact_mapping(payload)

    assert count == 3
    assert scrubbed["user"] == REDACTED_EMAIL
    assert scrubbed["items"] == [REDACTED_SSN, 42]


def test_google_cloud_dlp_client_deidentifies_via_sdk_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeSdkResponse:
        class item:
            value = "Customer [DLP_NAME] called"

    class _FakeSdkClient:
        def deidentify_content(self, *, request: dict[str, object]) -> _FakeSdkResponse:
            assert request["parent"] == "projects/demo-project/locations/global"
            return _FakeSdkResponse()

    class _FakeDlpModule:
        DlpServiceClient = _FakeSdkClient

    monkeypatch.setattr(
        "app.services.redaction.importlib.import_module",
        lambda name: _FakeDlpModule() if name == "google.cloud.dlp_v2" else None,
    )
    adapter = GoogleCloudDlpClient()

    assert adapter.deidentify_text("Customer Alice Smith called", project_id="demo-project") == (
        "Customer [DLP_NAME] called"
    )
