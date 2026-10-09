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

"""FastAPI entrypoint serving ADK web/API routes, A2A endpoints, and Agent Runtime routes."""

from __future__ import annotations

import contextlib
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Final

import uvicorn
from a2a.server.tasks import InMemoryTaskStore
from dotenv import load_dotenv
from fastapi import FastAPI
from google.adk.cli.fast_api import get_fast_api_app
from google.adk.runners import Runner

from app.agent import app as adk_app
from app.agent import root_agent
from app.app_utils import services
from app.app_utils.a2a import attach_a2a_routes
from app.app_utils.reasoning_engine_adapter import attach_reasoning_engine_routes
from app.constants import PROJECT_DISPLAY_NAME, PROJECT_NAME
from app.services.memory_service import get_shared_memory_service
from app.services.observability import configure_structured_logging

load_dotenv()
configure_structured_logging(project_id=os.getenv("GOOGLE_CLOUD_PROJECT", ""))

_ALLOW_ORIGINS_ENV: Final[str] = os.getenv("ALLOW_ORIGINS", "")
ALLOW_ORIGINS: Final[list[str] | None] = (
    [origin.strip() for origin in _ALLOW_ORIGINS_ENV.split(",") if origin.strip()]
    if _ALLOW_ORIGINS_ENV
    else None
)
OTEL_TO_CLOUD: Final[bool] = os.environ.get(
    "GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY", ""
).lower() in ("true", "1")
AGENT_DIR: Final[Path] = Path(__file__).resolve().parent
"""The `app` package itself. ADK treats a directory holding `agent.py` as a single agent, so
`/list-apps` and the web UI expose only `app` rather than each `app/agents/<name>/agent.py`."""


@contextlib.asynccontextmanager
async def lifespan(fastapi_app: FastAPI) -> AsyncIterator[None]:
    """Initialize the shared ADK Runner and attach A2A routes on application startup.

    Args:
        fastapi_app: FastAPI application instance.

    Yields:
        Control to the active FastAPI lifespan context.
    """
    runner = Runner(
        app=adk_app,
        session_service=services.get_session_service(),
        artifact_service=services.get_artifact_service(),
        memory_service=services.get_memory_service(),
        auto_create_session=True,
    )
    fastapi_app.state.runner = runner
    fastapi_app.state.agent_app_name = adk_app.name
    if not getattr(fastapi_app.state, "a2a_routes_attached", False):
        await attach_a2a_routes(
            fastapi_app,
            agent=root_agent,
            runner=runner,
            task_store=InMemoryTaskStore(),
            rpc_path=f"/a2a/{adk_app.name}",
        )
        fastapi_app.state.a2a_routes_attached = True
    try:
        yield
    finally:
        await get_shared_memory_service().drain()
        await runner.close()


app: FastAPI = get_fast_api_app(
    agents_dir=str(AGENT_DIR),
    web=True,
    artifact_service_uri=services.ARTIFACT_SERVICE_URI,
    memory_service_uri=services.MEMORY_SERVICE_URI,
    allow_origins=ALLOW_ORIGINS,
    session_service_uri=services.SESSION_SERVICE_URI,
    otel_to_cloud=OTEL_TO_CLOUD,
    lifespan=lifespan,
)
app.title = PROJECT_NAME
app.description = f"{PROJECT_DISPLAY_NAME}: ADK agent with A2A and runtime API routes"

attach_reasoning_engine_routes(app)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
