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

"""Unit tests for the `gh` CLI wrapper: invocation, decoding and pagination."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping, Sequence

import pytest

from scripts.ci import github_api


class _FakeCompleted:
    """Stands in for ``subprocess.CompletedProcess`` without invoking a binary."""

    def __init__(self, *, returncode: int, stdout: str, stderr: str) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _install_fake_gh(
    monkeypatch: pytest.MonkeyPatch,
    *,
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
    recorder: list[Sequence[str]] | None = None,
) -> None:
    """Replaces ``subprocess.run`` so no `gh` binary and no network are needed."""

    def _fake_run(command: Sequence[str], **kwargs: object) -> _FakeCompleted:
        if recorder is not None:
            recorder.append(list(command))
        return _FakeCompleted(returncode=returncode, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(subprocess, "run", _fake_run)


def test_request_sends_the_payload_over_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: list[Sequence[str]] = []
    _install_fake_gh(
        monkeypatch, stdout='{"html_url": "https://example.invalid/c/1"}', recorder=recorded
    )

    response = github_api.request("PATCH", "repos/o/r/issues/comments/1", payload={"body": "x"})

    assert github_api.field_str(github_api.as_mapping(response), "html_url").endswith("/c/1")
    assert recorded[0] == [
        "gh",
        "api",
        "--method",
        "PATCH",
        "repos/o/r/issues/comments/1",
        "--input",
        "-",
    ]


def test_request_omits_the_input_flag_without_a_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: list[Sequence[str]] = []
    _install_fake_gh(monkeypatch, stdout="{}", recorder=recorded)

    github_api.request("GET", "repos/o/r/pulls/1")

    assert "--input" not in recorded[0]


@pytest.mark.parametrize(
    ("stdout", "expected"),
    [
        pytest.param("", {}, id="empty_body_from_204"),
        pytest.param("   \n", {}, id="whitespace_only_body"),
        pytest.param('{"a": 1}', {"a": 1}, id="object_body"),
    ],
)
def test_decoding_tolerates_empty_responses(
    monkeypatch: pytest.MonkeyPatch, stdout: str, expected: object
) -> None:
    _install_fake_gh(monkeypatch, stdout=stdout)
    assert github_api.request("GET", "repos/o/r") == expected


def test_non_zero_exit_surfaces_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_gh(monkeypatch, returncode=1, stderr="HTTP 404: Not Found")
    with pytest.raises(github_api.GitHubApiError, match="HTTP 404"):
        github_api.request("GET", "repos/o/r/pulls/999")


def test_non_json_response_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_gh(monkeypatch, stdout="<html>gateway timeout</html>")
    with pytest.raises(github_api.GitHubApiError, match="non-JSON payload"):
        github_api.request("GET", "repos/o/r")


def test_paginate_flattens_every_page(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_gh(monkeypatch, stdout=json.dumps([[{"id": 1}, {"id": 2}], [{"id": 3}]]))
    items = github_api.paginate("repos/o/r/pulls/1/comments")
    assert [github_api.field_int(github_api.as_mapping(i), "id") for i in items] == [1, 2, 3]


@pytest.mark.parametrize(
    ("stdout", "error_match"),
    [
        pytest.param('{"not": "a list"}', "array of pages", id="slurp_must_be_a_list"),
        pytest.param('[{"not": "a list"}]', "array page", id="each_page_must_be_a_list"),
    ],
)
def test_paginate_rejects_unexpected_shapes(
    monkeypatch: pytest.MonkeyPatch, stdout: str, error_match: str
) -> None:
    _install_fake_gh(monkeypatch, stdout=stdout)
    with pytest.raises(github_api.GitHubApiError, match=error_match):
        github_api.paginate("repos/o/r/pulls/1/files")


@pytest.mark.parametrize(
    "value",
    [
        pytest.param([], id="list"),
        pytest.param("text", id="string"),
        pytest.param(7, id="number"),
        pytest.param(None, id="null"),
    ],
)
def test_as_mapping_rejects_non_objects(value: object) -> None:
    with pytest.raises(github_api.GitHubApiError, match="Expected a JSON object"):
        github_api.as_mapping(value)


@pytest.mark.parametrize(
    ("source", "key", "expected"),
    [
        pytest.param({"body": "hello"}, "body", "hello", id="present_string"),
        pytest.param({"body": None}, "body", "", id="null_falls_back"),
        pytest.param({"body": 12}, "body", "", id="wrong_type_falls_back"),
        pytest.param({}, "body", "", id="absent_falls_back"),
    ],
)
def test_field_str(source: Mapping[str, object], key: str, expected: str) -> None:
    assert github_api.field_str(source, key) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param({"id": 42}, 42, id="present_int"),
        pytest.param({"id": True}, 0, id="bool_is_not_an_int"),
        pytest.param({"id": "42"}, 0, id="numeric_string_falls_back"),
        pytest.param({}, 0, id="absent_falls_back"),
    ],
)
def test_field_int(source: Mapping[str, object], expected: int) -> None:
    assert github_api.field_int(source, "id") == expected
