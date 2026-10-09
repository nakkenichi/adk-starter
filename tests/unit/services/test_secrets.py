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

"""Unit tests for SecretManagerService."""

from __future__ import annotations

import pytest

from app.models.exceptions import SecretAccessError
from app.services.secrets import SecretManagerService


class _FakeSecretClient:
    def __init__(self, payloads: dict[str, str]) -> None:
        self.payloads = payloads
        self.calls: list[str] = []

    def access_secret_text(self, resource_name: str) -> str:
        self.calls.append(resource_name)
        return self.payloads[resource_name]


def test_get_secret_resolves_and_caches_secret_value() -> None:
    resource = "projects/my-proj/secrets/webhook-token/versions/latest"
    client = _FakeSecretClient({resource: "  super-secret-value  "})
    service = SecretManagerService(project_id="my-proj", client=client)

    first = service.get_secret("webhook-token")
    second = service.get_secret("webhook-token")

    assert first == "super-secret-value"
    assert second == "super-secret-value"
    assert client.calls == [resource]


@pytest.mark.parametrize(
    ("project_id", "secret_id"),
    [
        pytest.param("", "webhook-token", id="missing_project"),
        pytest.param("my-proj", "   ", id="empty_secret_id"),
    ],
)
def test_get_secret_rejects_invalid_identifiers(project_id: str, secret_id: str) -> None:
    service = SecretManagerService(project_id=project_id, client=_FakeSecretClient({}))

    with pytest.raises(SecretAccessError) as exc_info:
        service.get_secret(secret_id)

    assert exc_info.value.recovery_hint


def test_resolve_env_or_secret_fetches_from_secret_manager_when_secret_id_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SESSION_SERVICE_URI", raising=False)
    monkeypatch.setenv("SESSION_SERVICE_URI_SECRET_ID", "prod-session-uri")
    resource = "projects/my-proj/secrets/prod-session-uri/versions/latest"
    client = _FakeSecretClient({resource: "postgresql+asyncpg://host/db"})
    service = SecretManagerService(project_id="my-proj", client=client)

    resolved = service.resolve_env_or_secret(
        direct_env_var="SESSION_SERVICE_URI",
        secret_id_env_var="SESSION_SERVICE_URI_SECRET_ID",
    )

    assert resolved == "postgresql+asyncpg://host/db"
    assert client.calls == [resource]
