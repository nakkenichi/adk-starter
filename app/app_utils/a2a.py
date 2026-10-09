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

"""Attach A2A (Agent2Agent) endpoints to the FastAPI application."""

from __future__ import annotations

import os
from typing import Final

from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
)
from a2a.server.tasks import TaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentExtension, AgentInterface
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH
from fastapi import FastAPI
from google.adk.a2a.executor.a2a_agent_executor import A2aAgentExecutor
from google.adk.a2a.utils.agent_card_builder import AgentCardBuilder
from google.adk.agents import BaseAgent
from google.adk.runners import Runner

from app.app_utils.services import _resolve_agent_engine_location

_ADK_AGENT_EXECUTOR_EXTENSION_URI: Final[str] = (
    "https://google.github.io/adk-docs/a2a/a2a-extension/"
)


async def _add_v0_3_compat_interface(card: AgentCard) -> AgentCard:
    """Advertise a v0.3 JSON-RPC interface for A2A backward compatibility.

    Args:
        card: Built AgentCard instance.

    Returns:
        Updated AgentCard with v0.3 JSONRPC interface appended once.
    """
    if card.supported_interfaces:
        already_present = any(
            iface.protocol_binding == "JSONRPC" and iface.protocol_version == "0.3"
            for iface in card.supported_interfaces
        )
        if not already_present:
            card.supported_interfaces.append(
                AgentInterface(
                    protocol_binding="JSONRPC",
                    protocol_version="0.3",
                    url=card.supported_interfaces[0].url,
                )
            )
    return card


def _default_capabilities() -> AgentCapabilities:
    """Return default A2A capabilities advertising streaming and ADK executor support.

    Returns:
        Configured AgentCapabilities instance.
    """
    return AgentCapabilities(
        streaming=True,
        extensions=[
            AgentExtension(
                uri=_ADK_AGENT_EXECUTOR_EXTENSION_URI,
                description="Ability to use the new agent executor implementation",
            ),
        ],
    )


def resolve_app_url(app_url: str | None = None) -> str:
    """Resolve the public base URL advertised inside the A2A agent card.

    Args:
        app_url: Optional explicit base URL override.

    Returns:
        Resolved public base URL string without a trailing slash.
    """
    if app_url:
        return app_url.rstrip("/")
    if env_url := os.getenv("APP_URL"):
        return env_url.rstrip("/")

    agent_engine_id = os.getenv("GOOGLE_CLOUD_AGENT_ENGINE_ID")
    project = os.getenv("GOOGLE_CLOUD_PROJECT")
    location = _resolve_agent_engine_location()
    if agent_engine_id and project and location:
        return (
            f"https://{location}-aiplatform.googleapis.com/reasoningEngines/v1"
            f"/projects/{project}/locations/{location}"
            f"/reasoningEngines/{agent_engine_id}/api"
        )

    return "http://0.0.0.0:8080"


async def attach_a2a_routes(
    app: FastAPI,
    *,
    agent: BaseAgent,
    runner: Runner,
    task_store: TaskStore,
    rpc_path: str,
    capabilities: AgentCapabilities | None = None,
    agent_version: str | None = None,
    app_url: str | None = None,
) -> None:
    """Register A2A JSON-RPC and agent-card routes under rpc_path.

    Args:
        app: Target FastAPI application.
        agent: Root ADK agent to expose over A2A.
        runner: Shared ADK Runner instance.
        task_store: A2A TaskStore implementation.
        rpc_path: URL path prefix for A2A RPC routes.
        capabilities: Optional AgentCapabilities override.
        agent_version: Optional version string override.
        app_url: Optional public URL override.
    """
    resolved_app_url = resolve_app_url(app_url)
    resolved_agent_version = agent_version or os.getenv("AGENT_VERSION", "0.1.0")
    resolved_capabilities = capabilities or _default_capabilities()

    agent_card = await AgentCardBuilder(
        agent=agent,
        capabilities=resolved_capabilities,
        rpc_url=f"{resolved_app_url}{rpc_path}",
        agent_version=resolved_agent_version,
    ).build()

    request_handler = DefaultRequestHandler(
        agent_executor=A2aAgentExecutor(runner=runner),
        task_store=task_store,
        agent_card=agent_card,
    )

    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(
            agent_card,
            card_modifier=_add_v0_3_compat_interface,
            card_url=f"{rpc_path}{AGENT_CARD_WELL_KNOWN_PATH}",
        ),
        jsonrpc_routes=create_jsonrpc_routes(
            request_handler,
            rpc_url=rpc_path,
            enable_v0_3_compat=True,
        ),
    )
