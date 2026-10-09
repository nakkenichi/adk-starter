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

"""ADK plugins enforcing cross-cutting guardrails, memory compaction, and observability."""

from __future__ import annotations

from app.plugins.guards import DEFAULT_HIGH_STAKES_TOOLS, DomainErrorGuardPlugin
from app.plugins.memory import AsyncMemoryConsolidationPlugin
from app.plugins.observability import ObservabilityPlugin

__all__ = [
    "DEFAULT_HIGH_STAKES_TOOLS",
    "AsyncMemoryConsolidationPlugin",
    "DomainErrorGuardPlugin",
    "ObservabilityPlugin",
]
