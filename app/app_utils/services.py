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

"""Process-wide ADK session, memory, and artifact services shared across serving surfaces."""

from __future__ import annotations

import functools
import os
from pathlib import Path
from typing import Final

from google.adk.artifacts import (
    BaseArtifactService,
    GcsArtifactService,
    InMemoryArtifactService,
)
from google.adk.cli.service_registry import get_service_registry
from google.adk.cli.utils.service_factory import create_session_service_from_options
from google.adk.memory.base_memory_service import BaseMemoryService
from google.adk.memory.in_memory_memory_service import InMemoryMemoryService
from google.adk.memory.vertex_ai_memory_bank_service import VertexAiMemoryBankService
from google.adk.sessions.base_session_service import BaseSessionService
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.adk.sessions.vertex_ai_session_service import VertexAiSessionService

from app.services.secrets import SecretManagerService

SESSION_SERVICE_URI: Final[str] = "shared://session"
ARTIFACT_SERVICE_URI: Final[str] = "shared://artifact"
MEMORY_SERVICE_URI: Final[str] = "shared://memory"

_AGENT_DIR: Final[Path] = Path(__file__).resolve().parents[1]


def _resolve_agent_engine_location() -> str:
    """Resolves a regional runtime location, ignoring `'global'`."""
    for env_key in ("GOOGLE_CLOUD_AGENT_ENGINE_LOCATION", "GOOGLE_CLOUD_LOCATION"):
        candidate = os.environ.get(env_key, "").strip()
        if candidate and candidate.lower() != "global":
            return candidate
    return "us-central1"


@functools.cache
def get_session_service() -> BaseSessionService:
    """Return the process-wide session service shared across every serving surface.

    Resolves `SESSION_SERVICE_URI` directly or from Secret Manager when
    `SESSION_SERVICE_URI_SECRET_ID` is configured.

    Returns:
        Configured BaseSessionService instance.
    """
    secret_service = SecretManagerService()
    uri = secret_service.resolve_env_or_secret(
        direct_env_var="SESSION_SERVICE_URI",
        secret_id_env_var="SESSION_SERVICE_URI_SECRET_ID",
    )
    if uri and not uri.startswith("shared://"):
        return create_session_service_from_options(
            base_dir=str(_AGENT_DIR),
            session_service_uri=uri,
        )
    if agent_engine_id := os.environ.get("GOOGLE_CLOUD_AGENT_ENGINE_ID"):
        return VertexAiSessionService(
            project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
            location=_resolve_agent_engine_location(),
            agent_engine_id=agent_engine_id,
        )
    return InMemorySessionService()


@functools.cache
def get_memory_service() -> BaseMemoryService:
    """Return the process-wide ADK memory service.

    Returns:
        Configured BaseMemoryService instance.
    """
    if agent_engine_id := os.environ.get("GOOGLE_CLOUD_AGENT_ENGINE_ID"):
        return VertexAiMemoryBankService(
            project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
            location=_resolve_agent_engine_location(),
            agent_engine_id=agent_engine_id,
        )
    return InMemoryMemoryService()


@functools.cache
def get_artifact_service() -> BaseArtifactService:
    """Return the process-wide artifact service.

    Returns:
        Configured BaseArtifactService instance.
    """
    if bucket := os.environ.get("LOGS_BUCKET_NAME"):
        return GcsArtifactService(bucket_name=bucket)
    return InMemoryArtifactService()


def _build_shared_session_service(uri: str, **kwargs: object) -> BaseSessionService:
    """Factory adapter registering shared:// session service in ADK service registry."""
    del uri, kwargs
    return get_session_service()


def _build_shared_memory_service(uri: str, **kwargs: object) -> BaseMemoryService:
    """Factory adapter registering shared:// memory service in ADK service registry."""
    del uri, kwargs
    return get_memory_service()


def _build_shared_artifact_service(uri: str, **kwargs: object) -> BaseArtifactService:
    """Factory adapter registering shared:// artifact service in ADK service registry."""
    del uri, kwargs
    return get_artifact_service()


_registry = get_service_registry()
_registry.register_session_service("shared", _build_shared_session_service)
_registry.register_memory_service("shared", _build_shared_memory_service)
_registry.register_artifact_service("shared", _build_shared_artifact_service)
