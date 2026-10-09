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

"""Minimal typed GitHub REST helpers backed by the preinstalled `gh` CLI.

The AI review workflow runs on a bare GitHub runner without the project virtual
environment, so this module intentionally depends only on the standard library
plus the `gh` binary (authenticated through the ``GH_TOKEN`` environment variable).
"""

from __future__ import annotations

import json
import logging
import subprocess
from collections.abc import Mapping, Sequence
from typing import Final

logger: Final = logging.getLogger(__name__)

_GH_TIMEOUT_SECONDS: Final[int] = 120


class GitHubApiError(RuntimeError):
    """Raised when a `gh` invocation fails or returns an unusable payload."""


def _run_gh(args: Sequence[str], *, stdin_payload: str = "") -> str:
    """Invokes the `gh` CLI and returns stdout, raising on a non-zero exit."""
    command = ["gh", *args]
    logger.debug("Invoking: gh %s", " ".join(args))
    completed = subprocess.run(
        command,
        input=stdin_payload,
        capture_output=True,
        text=True,
        timeout=_GH_TIMEOUT_SECONDS,
        check=False,
    )
    if completed.returncode != 0:
        raise GitHubApiError(
            f"`gh {' '.join(args)}` exited with {completed.returncode}: {completed.stderr.strip()}"
        )
    return completed.stdout


def _decode(raw: str) -> object:
    """Parses `gh` stdout as JSON, tolerating the empty body of a 204 response."""
    stripped = raw.strip()
    if not stripped:
        return {}
    try:
        return json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise GitHubApiError(f"GitHub returned a non-JSON payload: {stripped[:200]}") from exc


def request(
    method: str,
    endpoint: str,
    *,
    payload: Mapping[str, object] | None = None,
) -> object:
    """Performs a single GitHub REST call.

    Args:
        method: HTTP verb such as ``GET``, ``POST`` or ``PATCH``.
        endpoint: API path relative to the API root, e.g. ``repos/owner/name/pulls/1``.
        payload: Optional JSON request body, sent over stdin to avoid shell quoting issues.

    Returns:
        The decoded JSON response, or an empty mapping when the body is empty.

    Raises:
        GitHubApiError: If the CLI call fails or the response is not valid JSON.
    """
    args = ["api", "--method", method, endpoint]
    body = ""
    if payload is not None:
        args += ["--input", "-"]
        body = json.dumps(payload)
    return _decode(_run_gh(args, stdin_payload=body))


def paginate(endpoint: str) -> list[object]:
    """Fetches every page of a list endpoint and returns the concatenated items.

    Args:
        endpoint: API path relative to the API root; `gh` appends the pagination params.

    Returns:
        A flat list of the decoded JSON array elements across all pages.

    Raises:
        GitHubApiError: If the CLI call fails or a page is not a JSON array.
    """
    pages = _decode(_run_gh(["api", "--paginate", "--slurp", endpoint]))
    if not isinstance(pages, list):
        raise GitHubApiError(f"Expected a JSON array of pages from {endpoint}")
    items: list[object] = []
    for page in pages:
        if not isinstance(page, list):
            raise GitHubApiError(f"Expected a JSON array page from {endpoint}")
        items.extend(page)
    return items


def as_mapping(value: object) -> Mapping[str, object]:
    """Narrows a decoded JSON value to a string-keyed mapping.

    Args:
        value: A decoded JSON value of unknown shape.

    Returns:
        The value as a string-keyed mapping.

    Raises:
        GitHubApiError: If the value is not a JSON object.
    """
    if not isinstance(value, dict):
        raise GitHubApiError(f"Expected a JSON object, got {type(value).__name__}")
    return {str(key): item for key, item in value.items()}


def field_str(source: Mapping[str, object], key: str, *, default: str = "") -> str:
    """Reads a string field from a decoded JSON object, falling back to ``default``."""
    value = source.get(key)
    return value if isinstance(value, str) else default


def field_int(source: Mapping[str, object], key: str, *, default: int = 0) -> int:
    """Reads an integer field from a decoded JSON object, falling back to ``default``."""
    value = source.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else default
