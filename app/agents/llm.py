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

"""Model factory, safety settings, and complexity router shared across agents."""

from __future__ import annotations

from collections.abc import Callable

from google.adk.agents.callback_context import CallbackContext
from google.adk.models import Gemini
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from app.agents.constants import (
    DEFAULT_MODEL,
    FAST_MODEL,
    MODEL_RETRY_ATTEMPTS,
    PRO_MODEL,
)
from app.agents.state import ROUTED_MODEL_KEY
from app.models.enums import TaskComplexity


def select_model_for_complexity(complexity: TaskComplexity) -> str:
    """Routes a task complexity tier to the corresponding model identifier.

    Routine requests use the low-latency tier (`FAST_MODEL`), whereas complex
    planning and remediation requests use the reasoning-optimized tier (`PRO_MODEL`).

    Args:
        complexity: Discrete task complexity tier (`TaskComplexity.ROUTINE` or `COMPLEX`).

    Returns:
        Canonical model identifier from `app.agents.constants`.
    """
    if complexity == TaskComplexity.COMPLEX:
        return PRO_MODEL
    return FAST_MODEL


def default_safety_settings() -> list[types.SafetySetting]:
    """Returns baseline safety guardrail thresholds for model generations."""
    return [
        types.SafetySetting(
            category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
            threshold=types.HarmBlockThreshold.BLOCK_LOW_AND_ABOVE,
        ),
        types.SafetySetting(
            category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
            threshold=types.HarmBlockThreshold.BLOCK_LOW_AND_ABOVE,
        ),
        types.SafetySetting(
            category=types.HarmCategory.HARM_CATEGORY_HARASSMENT,
            threshold=types.HarmBlockThreshold.BLOCK_LOW_AND_ABOVE,
        ),
        types.SafetySetting(
            category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
            threshold=types.HarmBlockThreshold.BLOCK_LOW_AND_ABOVE,
        ),
    ]


def default_generate_content_config() -> types.GenerateContentConfig:
    """Builds the default GenerateContentConfig populated with safety settings."""
    return types.GenerateContentConfig(safety_settings=default_safety_settings())


def build_gemini(model: str = DEFAULT_MODEL) -> Gemini:
    """Builds a model wrapper configured with the project-wide retry policy.

    Args:
        model: Model identifier, e.g. `FAST_MODEL` or `PRO_MODEL`.

    Returns:
        Configured model instance with transient-error retries enabled.
    """
    return Gemini(
        model=model,
        retry_options=types.HttpRetryOptions(attempts=MODEL_RETRY_ATTEMPTS),
    )


def make_model_router_callback(
    model: str,
) -> Callable[[CallbackContext, LlmRequest], LlmResponse | None]:
    """Creates a `before_model_callback` recording the active `model` tier in session state."""

    def _record_routed_model(
        callback_context: CallbackContext,
        llm_request: LlmRequest,
    ) -> LlmResponse | None:
        del llm_request
        callback_context.state[ROUTED_MODEL_KEY] = model
        return None

    return _record_routed_model
