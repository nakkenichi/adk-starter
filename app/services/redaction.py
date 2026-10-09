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

"""PII and credential redaction service for logging and memory pipelines.

Combines deterministic regex scrubbing with an optional DLP de-identification
adapter so logs and memories are scrubbed before storage.
"""

from __future__ import annotations

import importlib
import logging
import os
import re
from typing import Final, Protocol

_logger: Final[logging.Logger] = logging.getLogger(__name__)

_URL_USERINFO_RE: Final[re.Pattern[str]] = re.compile(
    r"\b([a-zA-Z][a-zA-Z0-9+.\-]*://)[^\s'\"/?#]*:[^\s'\"/@?#]+@"
)
_EMAIL_RE: Final[re.Pattern[str]] = re.compile(
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"
)
_SSN_RE: Final[re.Pattern[str]] = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_PHONE_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<!\d)(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}(?!\d)"
)
_CREDIT_CARD_RE: Final[re.Pattern[str]] = re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b")
_BEARER_TOKEN_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:Bearer\s+[0-9A-Za-z._\-+/=~]+|"
    r"(?:ya29\.|AIza[0-9A-Za-z_\-]{10,}|ghp_[0-9A-Za-z]{10,}|sk-[0-9A-Za-z_\-]{10,})"
    r"[0-9A-Za-z._\-+/=~]*)"
)

REDACTED_USERINFO: Final[str] = "<redacted>@"
REDACTED_EMAIL: Final[str] = "[REDACTED_EMAIL]"
REDACTED_SSN: Final[str] = "[REDACTED_SSN]"
REDACTED_PHONE: Final[str] = "[REDACTED_PHONE]"
REDACTED_CARD: Final[str] = "[REDACTED_CARD]"
REDACTED_TOKEN: Final[str] = "[REDACTED_SECRET]"

DEFAULT_DLP_INFO_TYPES: Final[tuple[str, ...]] = (
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "US_SOCIAL_SECURITY_NUMBER",
    "CREDIT_CARD_NUMBER",
    "PERSON_NAME",
)


class DlpDeidentifyClient(Protocol):
    """Protocol for remote DLP text de-identification."""

    def deidentify_text(self, text: str, *, project_id: str) -> str:
        """Returns text scrubbed by the DLP de-identification service."""


class _DlpSdkClientProtocol(Protocol):
    """Structural protocol for the underlying DLP SDK client."""

    def deidentify_content(self, *, request: dict[str, object]) -> object: ...


class GoogleCloudDlpClient:
    """DLP de-identification adapter for `projects/<project>/content:deidentify`."""

    def __init__(self, *, info_types: tuple[str, ...] = DEFAULT_DLP_INFO_TYPES) -> None:
        self._info_types: tuple[str, ...] = info_types
        self._client: _DlpSdkClientProtocol | None = None

    def _get_client(self) -> _DlpSdkClientProtocol | None:
        """Lazily initializes and caches the `DlpServiceClient` instance."""
        if self._client is not None:
            return self._client
        try:
            dlp_module = importlib.import_module("google.cloud.dlp_v2")
        except ImportError:
            return None
        client_cls = getattr(dlp_module, "DlpServiceClient", None)
        if not callable(client_cls):
            return None
        instance: _DlpSdkClientProtocol = client_cls()
        self._client = instance
        return self._client

    def deidentify_text(self, text: str, *, project_id: str) -> str:
        """De-identifies `text` via `DlpServiceClient`."""
        clean_project = project_id.strip()
        if not text or not clean_project:
            return text
        try:
            client = self._get_client()
            if client is None:
                return text
            parent = f"projects/{clean_project}/locations/global"
            inspect_config = {
                "info_types": [{"name": name} for name in self._info_types],
            }
            deidentify_config: dict[str, object] = {
                "info_type_transformations": {
                    "transformations": [
                        {
                            "primitive_transformation": {
                                "replace_with_info_type_config": {},
                            }
                        }
                    ]
                }
            }
            response = client.deidentify_content(
                request={
                    "parent": parent,
                    "inspect_config": inspect_config,
                    "deidentify_config": deidentify_config,
                    "item": {"value": text},
                }
            )
            item = getattr(response, "item", None)
            value = getattr(item, "value", None)
            return str(value) if isinstance(value, str) else text
        except Exception as exc:
            _logger.debug("DLP deidentify_content failed; using regex-scrubbed text: %s", exc)
            return text


class PiiRedactionService:
    """Scrubs PII and inline credentials from text and structured payloads before storage."""

    def __init__(
        self,
        *,
        dlp_client: DlpDeidentifyClient | None = None,
        project_id: str | None = None,
    ) -> None:
        """Initializes the PII redaction service.

        Args:
            dlp_client: Optional DLP client. When omitted and `ENABLE_CLOUD_DLP=true`,
                `GoogleCloudDlpClient` is wired automatically.
            project_id: Project ID passed to the DLP client when present.
        """
        resolved_project = (
            os.environ.get("GOOGLE_CLOUD_PROJECT", "") if project_id is None else project_id
        )
        self._project_id: str = resolved_project.strip()
        if dlp_client is not None:
            self._dlp_client: DlpDeidentifyClient | None = dlp_client
        elif os.environ.get("ENABLE_CLOUD_DLP", "").lower() in {"1", "true", "yes"}:
            self._dlp_client = GoogleCloudDlpClient()
        else:
            self._dlp_client = None

    def redact_text(self, text: str) -> tuple[str, int]:
        """Scrubs URL credentials, emails, SSNs, phone numbers, cards, and tokens from text.

        Args:
            text: Raw input text.

        Returns:
            Tuple of `(redacted_text, replacement_count)`.
        """
        if not text:
            return "", 0

        scrubbed, n_urls = _URL_USERINFO_RE.subn(
            lambda match: match.group(1) + REDACTED_USERINFO,
            text,
        )
        scrubbed, n_tokens = _BEARER_TOKEN_RE.subn(REDACTED_TOKEN, scrubbed)
        scrubbed, n_emails = _EMAIL_RE.subn(REDACTED_EMAIL, scrubbed)
        scrubbed, n_ssns = _SSN_RE.subn(REDACTED_SSN, scrubbed)
        scrubbed, n_cards = _CREDIT_CARD_RE.subn(REDACTED_CARD, scrubbed)
        scrubbed, n_phones = _PHONE_RE.subn(REDACTED_PHONE, scrubbed)
        total = n_urls + n_tokens + n_emails + n_ssns + n_cards + n_phones

        if self._dlp_client is not None and self._project_id:
            try:
                dlp_result = self._dlp_client.deidentify_text(
                    scrubbed,
                    project_id=self._project_id,
                )
            except Exception as exc:
                _logger.debug("DLP client raised unexpected error: %s", exc)
                dlp_result = scrubbed
            if dlp_result != scrubbed:
                total += 1
                scrubbed = dlp_result

        return scrubbed, total

    def redact_mapping(self, payload: dict[str, object]) -> tuple[dict[str, object], int]:
        """Recursively scrubs string values inside a JSON-compatible dictionary.

        Args:
            payload: Input dictionary.

        Returns:
            Tuple of `(redacted_dict, total_replacements)`.
        """
        out: dict[str, object] = {}
        total = 0
        for key, value in payload.items():
            redacted_val, count = self._redact_value(value)
            out[key] = redacted_val
            total += count
        return out, total

    def _redact_value(self, value: object) -> tuple[object, int]:
        if isinstance(value, str):
            return self.redact_text(value)
        if isinstance(value, (list, tuple, set)):
            items: list[object] = []
            total = 0
            for item in value:
                redacted_item, count = self._redact_value(item)
                items.append(redacted_item)
                total += count
            return (tuple(items) if isinstance(value, tuple) else items), total
        if isinstance(value, dict):
            str_dict: dict[str, object] = {str(k): v for k, v in value.items()}
            return self.redact_mapping(str_dict)
        return value, 0
