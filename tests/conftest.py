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

"""Pytest offline mock fixtures and network egress interception for PR CI.

Every unit test runs with:
* outbound network blocked (loopback only), so an accidental live call fails loudly;
* a sanitized environment (no developer buckets, engines or URLs leak into tests);
* ``google.auth.default`` mocked, so no Application Default Credentials are needed;
* Gemini ``generate_content`` calls intercepted with a canned response.
"""

from __future__ import annotations

import asyncio
import inspect
import socket
from collections.abc import AsyncGenerator, Generator, Sequence
from pathlib import Path
from typing import Protocol
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from google.adk.sessions import InMemorySessionService
from google.genai import types

ALLOWED_HOSTS: set[str] = {"127.0.0.1", "::1", "localhost"}

# Variables that select live backends; they must never leak from a developer shell into tests.
_ISOLATED_ENV_VARS: tuple[str, ...] = (
    "LOGS_BUCKET_NAME",
    "EXPORTS_BUCKET_NAME",
    "SESSION_SERVICE_URI",
    "GOOGLE_CLOUD_AGENT_ENGINE_ID",
    "GOOGLE_CLOUD_AGENT_ENGINE_LOCATION",
    "APP_URL",
    "AGENT_VERSION",
    "ALLOW_ORIGINS",
    "GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY",
)


class OfflineNetworkEgressBlockedError(RuntimeError):
    """Raised when an external network connection is attempted during offline tests."""


class CannedResponseFactory(Protocol):
    """Callable building a canned Gemini response (see the canned_response_factory fixture)."""

    def __call__(
        self,
        text: str = ...,
        finish_reason: types.FinishReason = ...,
        prompt_tokens: int = ...,
        candidate_tokens: int = ...,
    ) -> types.GenerateContentResponse: ...


@pytest.fixture(autouse=True)
def block_external_network() -> Generator[None, None, None]:
    """Disable all outbound network calls, allowing only local loopback connections."""
    orig_connect, orig_getaddrinfo = socket.socket.connect, socket.getaddrinfo

    def guarded_connect(sock: socket.socket, address: tuple[object, ...] | str | bytes) -> None:
        host = address[0] if isinstance(address, tuple) and address else address
        if host not in ALLOWED_HOSTS:
            raise OfflineNetworkEgressBlockedError(f"Network egress blocked: {address!r} denied.")
        orig_connect(sock, address)

    def guarded_getaddrinfo(
        host: str | bytes | None,
        port: str | int | None,
        *args: int,
        **kwargs: int,
    ) -> Sequence[tuple[object, ...]]:
        host_str = host.decode() if isinstance(host, bytes) else host
        if host_str is not None and host_str not in ALLOWED_HOSTS:
            raise OfflineNetworkEgressBlockedError(f"DNS resolution blocked: '{host_str}' denied.")
        return orig_getaddrinfo(host, port, *args, **kwargs)

    with (
        patch.object(socket.socket, "connect", guarded_connect),
        patch.object(socket, "getaddrinfo", guarded_getaddrinfo),
    ):
        yield


@pytest.fixture(autouse=True)
def mock_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sanitize environment variables and credentials for offline test isolation."""
    for key in _ISOLATED_ENV_VARS:
        monkeypatch.delenv(key, raising=False)
    for key, value in {
        "GOOGLE_API_KEY": "mock-offline-api-key",
        "GEMINI_API_KEY": "mock-offline-api-key",
        "GOOGLE_GENAI_USE_VERTEXAI": "false",
        "GOOGLE_GENAI_USE_ENTERPRISE": "false",
        "GOOGLE_CLOUD_PROJECT": "mock-offline-project",
        "GOOGLE_CLOUD_LOCATION": "global",
    }.items():
        monkeypatch.setenv(key, value)
    credentials = MagicMock()
    credentials.universe_domain = "googleapis.com"
    monkeypatch.setattr(
        "google.auth.default",
        lambda *args, **kwargs: (credentials, "mock-offline-project"),
    )


@pytest.fixture
def canned_response_factory() -> CannedResponseFactory:
    """Factory fixture generating schema-compliant Gemini responses."""

    def _create(
        text: str = "Mocked offline response",
        finish_reason: types.FinishReason = types.FinishReason.STOP,
        prompt_tokens: int = 10,
        candidate_tokens: int = 20,
    ) -> types.GenerateContentResponse:
        content = types.Content(role="model", parts=[types.Part.from_text(text=text)])
        usage = types.GenerateContentResponseUsageMetadata(
            prompt_token_count=prompt_tokens,
            candidates_token_count=candidate_tokens,
            total_token_count=prompt_tokens + candidate_tokens,
        )
        return types.GenerateContentResponse(
            candidates=[types.Candidate(content=content, finish_reason=finish_reason)],
            usage_metadata=usage,
            model_version="mock-gemini",
        )

    return _create


@pytest.fixture
def default_canned_response(
    canned_response_factory: CannedResponseFactory,
) -> types.GenerateContentResponse:
    """Provide a default mocked GenerateContentResponse."""
    return canned_response_factory()


@pytest.fixture(autouse=True)
def mock_genai_client(
    default_canned_response: types.GenerateContentResponse,
) -> Generator[MagicMock, None, None]:
    """Autouse fixture intercepting google.genai AsyncModels and Models generate_content."""
    async_mock = AsyncMock(return_value=default_canned_response)
    sync_mock = MagicMock(return_value=default_canned_response)

    async def mock_stream(
        *args: object, **kwargs: object
    ) -> AsyncGenerator[types.GenerateContentResponse, None]:
        del args, kwargs

        async def _gen() -> AsyncGenerator[types.GenerateContentResponse, None]:
            yield default_canned_response

        return _gen()

    with (
        patch("google.genai.models.AsyncModels.generate_content", new=async_mock),
        patch("google.genai.models.AsyncModels.generate_content_stream", new=mock_stream),
        patch("google.genai.models.Models.generate_content", new=sync_mock),
        patch(
            "google.genai.models.Models.generate_content_stream",
            new=MagicMock(return_value=[default_canned_response]),
        ),
    ):
        mock_client = MagicMock()
        mock_client.aio.models.generate_content = async_mock
        mock_client.models.generate_content = sync_mock
        yield mock_client


@pytest.fixture
def in_memory_session_service() -> InMemorySessionService:
    """Provide an isolated InMemorySessionService for unit tests."""
    return InMemorySessionService()


@pytest.fixture(scope="session")
def project_root() -> Path:
    """Repository root resolved from the conftest location."""
    return Path(__file__).resolve().parents[1]


def pytest_pyfunc_call(pyfuncitem: pytest.Function) -> bool | None:
    """Runs `async def` test functions via `asyncio.run` when pytest-asyncio is absent."""
    if inspect.iscoroutinefunction(pyfuncitem.obj):
        argnames = pyfuncitem._fixtureinfo.argnames
        kwargs = {name: pyfuncitem.funcargs[name] for name in argnames}
        asyncio.run(pyfuncitem.obj(**kwargs))
        return True
    return None
