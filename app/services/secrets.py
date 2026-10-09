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

"""Secret resolution and in-process caching service.

Resolves runtime secrets via `SecretManagerServiceClient` or an injected
`SecretVersionClient` protocol and caches resolved values in-process.
"""

from __future__ import annotations

import importlib
import os
import re
from typing import Final, Protocol

from app.models.exceptions import SecretAccessError

_FULL_SECRET_VERSION_RE: Final[re.Pattern[str]] = re.compile(
    r"^projects/[^/]+/secrets/[^/]+/versions/[^/]+$"
)
_FULL_SECRET_PATH_RE: Final[re.Pattern[str]] = re.compile(r"^projects/[^/]+/secrets/[^/]+$")


class SecretVersionClient(Protocol):
    """Protocol for accessing versioned secret payloads."""

    def access_secret_text(self, resource_name: str) -> str:
        """Fetches the secret payload string for `projects/*/secrets/*/versions/*`."""


class _SecretManagerSdkClientProtocol(Protocol):
    """Structural protocol for `SecretManagerServiceClient`."""

    def access_secret_version(self, *, request: dict[str, str]) -> object: ...


class GoogleCloudSecretManagerClient:
    """Secret Manager client backed by `SecretManagerServiceClient`."""

    def __init__(self) -> None:
        self._client: _SecretManagerSdkClientProtocol | None = None

    def _get_client(self) -> _SecretManagerSdkClientProtocol:
        """Lazily initializes and caches `SecretManagerServiceClient`."""
        if self._client is not None:
            return self._client
        try:
            sm_module = importlib.import_module("google.cloud.secretmanager")
        except ImportError as exc:
            raise SecretAccessError(
                "google-cloud-secret-manager is not installed in this environment."
            ) from exc

        client_cls = getattr(sm_module, "SecretManagerServiceClient", None)
        if not callable(client_cls):
            raise SecretAccessError("SecretManagerServiceClient is unavailable.")
        instance: _SecretManagerSdkClientProtocol = client_cls()
        self._client = instance
        return self._client

    def access_secret_text(self, resource_name: str) -> str:
        """Calls `SecretManagerServiceClient().access_secret_version` for `resource_name`.

        Args:
            resource_name: Full `projects/*/secrets/*/versions/*` resource path.

        Returns:
            Decoded UTF-8 secret payload string.

        Raises:
            SecretAccessError: If the SDK is unavailable or the RPC fails.
        """
        client = self._get_client()
        response = client.access_secret_version(request={"name": resource_name})
        payload = getattr(response, "payload", None)
        data = getattr(payload, "data", b"")
        if isinstance(data, bytes):
            return data.decode("utf-8")
        return str(data)


class SecretManagerService:
    """Retrieves and caches secrets by ID or resource path."""

    def __init__(
        self,
        *,
        project_id: str | None = None,
        client: SecretVersionClient | None = None,
    ) -> None:
        """Initializes the Secret Manager service.

        Args:
            project_id: Project ID (defaults to `GOOGLE_CLOUD_PROJECT`).
            client: Secret Manager client (defaults to `GoogleCloudSecretManagerClient()`).
        """
        resolved_project = (
            os.environ.get("GOOGLE_CLOUD_PROJECT", "") if project_id is None else project_id
        )
        self._project_id: str = resolved_project.strip()
        self._client: SecretVersionClient = (
            GoogleCloudSecretManagerClient() if client is None else client
        )
        self._cache: dict[str, str] = {}

    def build_secret_version_name(self, secret_id: str, *, version: str = "latest") -> str:
        """Builds the canonical `projects/<project>/secrets/<id>/versions/<ver>` path.

        Args:
            secret_id: Secret Manager secret ID or full `projects/*/secrets/*` resource path.
            version: Version tag or number (defaults to `"latest"`).

        Returns:
            Full Secret Manager version resource name.

        Raises:
            SecretAccessError: If `project_id` or `secret_id` is empty.
        """
        clean_id = secret_id.strip()
        clean_version = version.strip() or "latest"
        if not clean_id:
            raise SecretAccessError("secret_id must not be empty.")
        if _FULL_SECRET_VERSION_RE.match(clean_id):
            return clean_id
        if _FULL_SECRET_PATH_RE.match(clean_id):
            return f"{clean_id}/versions/{clean_version}"
        if not self._project_id:
            raise SecretAccessError("GOOGLE_CLOUD_PROJECT is not configured for Secret Manager.")
        return f"projects/{self._project_id}/secrets/{clean_id}/versions/{clean_version}"

    def get_secret(self, secret_id: str, *, version: str = "latest") -> str:
        """Retrieves and caches a secret value from Secret Manager.

        Args:
            secret_id: Secret identifier or resource path.
            version: Secret version (defaults to `"latest"`).

        Returns:
            Resolved non-empty secret string.

        Raises:
            SecretAccessError: If the secret lookup fails or resolves to an empty string.
        """
        resource_name = self.build_secret_version_name(secret_id, version=version)
        if resource_name in self._cache:
            return self._cache[resource_name]
        try:
            value = self._client.access_secret_text(resource_name).strip()
        except SecretAccessError:
            raise
        except Exception as exc:
            raise SecretAccessError(f"Failed to access secret {secret_id!r}: {exc}") from exc
        if not value:
            raise SecretAccessError(f"Secret {secret_id!r} resolved to an empty payload.")
        self._cache[resource_name] = value
        return value

    def resolve_env_or_secret(
        self,
        *,
        direct_env_var: str,
        secret_id_env_var: str,
    ) -> str | None:
        """Resolves a setting from a direct env var or a Secret Manager secret ID env var.

        When `secret_id_env_var` is set in the environment, fetches the secret payload from
        Secret Manager; otherwise falls back to `direct_env_var`.
        """
        if secret_id := os.environ.get(secret_id_env_var, "").strip():
            return self.get_secret(secret_id)
        if direct := os.environ.get(direct_env_var, "").strip():
            return direct
        return None
