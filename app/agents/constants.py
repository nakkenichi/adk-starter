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

"""Model and compaction configuration: single source of truth for model IDs and thresholds."""

from __future__ import annotations

from typing import Final

FAST_MODEL: Final[str] = "gemini-3-flash-preview"
"""Fast, cost-efficient model tier for routine triage, extraction, and lookup tasks."""

PRO_MODEL: Final[str] = "gemini-3.1-pro-preview"
"""High-reasoning model tier for complex multi-step planning and remediation synthesis."""

DEFAULT_MODEL: Final[str] = FAST_MODEL
"""Default model for agents when no explicit tier override is provided."""

MODEL_RETRY_ATTEMPTS: Final[int] = 3
"""Attempts (including the first call) for transient model API errors."""

COMPACTION_INTERVAL: Final[int] = 6
"""Number of new invocations before ADK EventsCompactionConfig summarizes older history."""

COMPACTION_OVERLAP_SIZE: Final[int] = 2
"""Number of overlapping invocations included when compacting adjacent windows."""

COMPACTION_TOKEN_THRESHOLD: Final[int] = 4000
"""Prompt token threshold triggering ADK native event compaction."""

COMPACTION_EVENT_RETENTION_SIZE: Final[int] = 4
"""Number of most-recent raw events retained verbatim after compaction."""
