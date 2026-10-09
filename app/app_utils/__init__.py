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

"""Serving utilities for A2A, runtime HTTP adapter, and ADK services."""

from __future__ import annotations

from app.app_utils.a2a import attach_a2a_routes, resolve_app_url
from app.app_utils.reasoning_engine_adapter import attach_reasoning_engine_routes
from app.app_utils.services import (
    ARTIFACT_SERVICE_URI,
    MEMORY_SERVICE_URI,
    SESSION_SERVICE_URI,
    get_artifact_service,
    get_memory_service,
    get_session_service,
)

__all__: list[str] = [
    "ARTIFACT_SERVICE_URI",
    "MEMORY_SERVICE_URI",
    "SESSION_SERVICE_URI",
    "attach_a2a_routes",
    "attach_reasoning_engine_routes",
    "get_artifact_service",
    "get_memory_service",
    "get_session_service",
    "resolve_app_url",
]
