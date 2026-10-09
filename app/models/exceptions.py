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

"""Fail-closed domain exceptions with guided LLM recovery hints.

Every expected, user-correctable failure derives from AgentDomainError so the
DomainErrorGuardPlugin can turn it into a structured tool response carrying an
actionable `recovery_hint` the LLM can follow. Anything else is treated as a bug
and propagates.
"""

from __future__ import annotations

from typing import Final

DEFAULT_RECOVERY_HINT: Final[str] = (
    "Inspect the error message, ask the user to clarify or correct the invalid parameter, "
    "and retry the tool call only after the input satisfies the schema constraints."
)


class AgentDomainError(Exception):
    """Base class for all expected domain errors raised by tools and services."""

    def __init__(
        self,
        message: str,
        *,
        recovery_hint: str = DEFAULT_RECOVERY_HINT,
    ) -> None:
        """Initializes the domain error with a descriptive recovery instruction.

        Args:
            message: Human- and model-readable description of what went wrong.
            recovery_hint: Guided recovery instruction telling the LLM how to recover.
        """
        super().__init__(message)
        self.recovery_hint: str = recovery_hint


class ExampleTaskError(AgentDomainError):
    """Raised when the analysis task receives invalid input."""

    def __init__(
        self,
        message: str,
        *,
        recovery_hint: str = (
            "Provide a non-empty `topic` between 1 and 200 characters and retry the tool call."
        ),
    ) -> None:
        super().__init__(message, recovery_hint=recovery_hint)


class StorageError(AgentDomainError):
    """Raised when an export cannot be written to object storage."""

    def __init__(
        self,
        message: str,
        *,
        recovery_hint: str = (
            "Verify that EXPORTS_BUCKET_NAME is configured and the local file is non-empty, "
            "or inform the user that cloud export is currently unavailable."
        ),
    ) -> None:
        super().__init__(message, recovery_hint=recovery_hint)


class SecretAccessError(AgentDomainError):
    """Raised when a secret cannot be retrieved from the secret store."""

    def __init__(
        self,
        message: str,
        *,
        recovery_hint: str = (
            "Verify the secret identifier exists in Secret Manager and the runtime service "
            "account holds roles/secretmanager.secretAccessor."
        ),
    ) -> None:
        super().__init__(message, recovery_hint=recovery_hint)


class PolicyViolationError(AgentDomainError):
    """Raised when a request or response violates security or safety guardrails."""

    def __init__(
        self,
        message: str,
        *,
        recovery_hint: str = (
            "Decline the unsafe instruction and ask the user to rephrase their request within "
            "permitted operational boundaries."
        ),
    ) -> None:
        super().__init__(message, recovery_hint=recovery_hint)
