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

"""Unit tests for hunk parsing, inline payload construction and publishing."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final

import pytest

from scripts.ci import github_api
from scripts.ci.publish_review import (
    _commentable_lines,
    _find_sticky,
    _inline_payloads,
    _posted_fingerprints,
    main,
)
from scripts.ci.review_render import STALE_MARKER
from scripts.ci.review_report import (
    FINGERPRINT_PREFIX,
    STICKY_MARKER,
    Finding,
    ReviewReport,
    Severity,
    encode_state,
)

_SINGLE_HUNK = "\n".join(
    [
        "@@ -10,3 +10,4 @@ def solve():",
        " context_before",
        "+added_one",
        "+added_two",
        " context_after",
    ]
)

_WITH_DELETIONS = "\n".join(
    [
        "@@ -1,4 +1,3 @@",
        " keep",
        "-removed_one",
        "-removed_two",
        " tail",
    ]
)

_TWO_HUNKS = "\n".join(
    [
        "@@ -1,2 +1,2 @@",
        " alpha",
        "+beta",
        "@@ -80,2 +90,2 @@",
        " gamma",
        "+delta",
    ]
)

_NO_NEWLINE = "\n".join(
    [
        "@@ -1 +1,2 @@",
        " first",
        "+second",
        "\\ No newline at end of file",
    ]
)


@pytest.mark.parametrize(
    ("patch", "expected"),
    [
        pytest.param(_SINGLE_HUNK, {10, 11, 12, 13}, id="context_and_added_lines"),
        pytest.param(_WITH_DELETIONS, {1, 2}, id="deletions_do_not_advance_cursor"),
        pytest.param(_TWO_HUNKS, {1, 2, 90, 91}, id="cursor_resets_per_hunk"),
        pytest.param(_NO_NEWLINE, {1, 2}, id="no_newline_marker_ignored"),
        pytest.param("", set(), id="empty_patch"),
        pytest.param("+orphan line without a hunk header", set(), id="lines_before_first_hunk"),
    ],
)
def test_commentable_lines(patch: str, expected: set[int]) -> None:
    assert _commentable_lines(patch) == expected


def _finding(
    *,
    severity: Severity = Severity.BLOCKING,
    path: str = "app/a.py",
    line: int = 11,
    title: str = "Problem",
) -> Finding:
    """Builds a finding with defaults matching the diff index used below."""
    return Finding(
        path=path,
        line=line,
        severity=severity,
        rule="3.1",
        title=title,
        body="Explanation.",
    )


_DIFF_INDEX: Mapping[str, set[int]] = {"app/a.py": {10, 11, 12}}


@pytest.mark.parametrize(
    ("findings", "already_posted", "expected_lines"),
    [
        pytest.param((_finding(),), set[str](), [11], id="blocking_is_posted_inline"),
        pytest.param(
            (_finding(severity=Severity.SUGGESTION),),
            set[str](),
            [11],
            id="suggestion_is_posted_inline",
        ),
        pytest.param(
            (_finding(severity=Severity.NIT),),
            set[str](),
            [],
            id="nit_stays_in_the_summary",
        ),
        pytest.param((_finding(line=99),), set[str](), [], id="line_outside_diff_is_dropped"),
        pytest.param(
            (_finding(path="app/untouched.py"),),
            set[str](),
            [],
            id="file_outside_diff_is_dropped",
        ),
        pytest.param(
            (_finding(),),
            {_finding().fingerprint},
            [],
            id="already_posted_is_skipped",
        ),
        pytest.param(
            (_finding(line=10, title="First"), _finding(line=12, title="Second")),
            set[str](),
            [10, 12],
            id="multiple_findings_are_batched",
        ),
    ],
)
def test_inline_payloads(
    findings: Sequence[Finding],
    already_posted: set[str],
    expected_lines: list[int],
) -> None:
    payloads = _inline_payloads(
        findings,
        diff_index=_DIFF_INDEX,
        already_posted=already_posted,
    )
    assert [payload["line"] for payload in payloads] == expected_lines
    assert all(payload["side"] == "RIGHT" for payload in payloads)


def test_inline_payload_embeds_the_fingerprint_for_deduplication() -> None:
    finding = _finding()
    payload = _inline_payloads((finding,), diff_index=_DIFF_INDEX, already_posted=set[str]())[0]
    body = payload["body"]
    assert isinstance(body, str)
    assert f"{FINGERPRINT_PREFIX}{finding.fingerprint}" in body
    assert "AGENTS.md §3.1" in body


@pytest.mark.parametrize(
    ("comments", "expected_id"),
    [
        pytest.param(
            [{"id": 1, "user": {"type": "Bot"}, "body": f"x {STICKY_MARKER} y"}],
            1,
            id="bot_authored_marker_is_accepted",
        ),
        pytest.param(
            [{"id": 2, "user": {"type": "User"}, "body": f"forged {STICKY_MARKER}"}],
            0,
            id="human_authored_marker_is_rejected",
        ),
        pytest.param(
            [
                {"id": 3, "user": {"type": "User"}, "body": f"forged {STICKY_MARKER}"},
                {"id": 4, "user": {"type": "Bot"}, "body": f"real {STICKY_MARKER}"},
            ],
            4,
            id="earlier_forgery_does_not_shadow_the_bot_comment",
        ),
        pytest.param(
            [{"id": 5, "user": None, "body": f"ghost {STICKY_MARKER}"}],
            0,
            id="missing_author_is_rejected",
        ),
        pytest.param(
            [{"id": 6, "user": {"type": "Bot"}, "body": "unrelated bot chatter"}],
            0,
            id="bot_comment_without_the_marker_is_ignored",
        ),
    ],
)
def test_find_sticky_requires_a_bot_author(
    monkeypatch: pytest.MonkeyPatch,
    comments: list[dict[str, object]],
    expected_id: int,
) -> None:
    monkeypatch.setattr(github_api, "paginate", lambda endpoint: [dict(item) for item in comments])
    found = _find_sticky(repo="o/r", pr=1)
    assert (github_api.field_int(found, "id") if found else 0) == expected_id


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class _Call:
    """A single recorded mutating call against the GitHub API."""

    method: str
    endpoint: str
    payload: Mapping[str, object]


class _FakeApi:
    """Serves canned list endpoints and records every mutation."""

    def __init__(self, pages: Mapping[str, list[object]]) -> None:
        self._pages = pages
        self.calls: list[_Call] = []

    def paginate(self, endpoint: str) -> list[object]:
        return list(self._pages.get(endpoint, []))

    def request(
        self,
        method: str,
        endpoint: str,
        *,
        payload: Mapping[str, object] | None = None,
    ) -> object:
        self.calls.append(_Call(method=method, endpoint=endpoint, payload=dict(payload or {})))
        return {"id": 99, "html_url": "https://example.test/sticky"}


_ISSUE_COMMENTS = "repos/o/r/issues/7/comments"
_PULL_FILES = "repos/o/r/pulls/7/files"
_PULL_COMMENTS = "repos/o/r/pulls/7/comments"
_PULL_REVIEWS = "repos/o/r/pulls/7/reviews"

_PATCH = "@@ -1,2 +10,3 @@\n context\n+added\n"


def _install_api(
    monkeypatch: pytest.MonkeyPatch,
    *,
    pages: Mapping[str, list[object]],
) -> _FakeApi:
    """Replaces the network layer and the repository slug for one test."""
    fake = _FakeApi(pages)
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setattr(github_api, "paginate", fake.paginate)
    monkeypatch.setattr(github_api, "request", fake.request)
    return fake


def _write_report(tmp_path: Path, payload: object) -> str:
    """Writes a review.json payload and returns its path."""
    target = tmp_path / "review.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    return str(target)


def _publish(findings_path: str) -> int:
    """Runs the publish sub-command through the real argument parser."""
    return main(
        [
            "publish",
            "--pr",
            "7",
            "--sha",
            "c0ffee1",
            "--findings",
            findings_path,
            "--run-url",
            "https://example.test/run",
        ]
    )


_ONE_SUGGESTION: Final[dict[str, object]] = {
    "findings": [
        {
            "path": "app/a.py",
            "line": 11,
            "severity": "suggestion",
            "rule": "3.1",
            "title": "Problem",
            "body": "Explanation.",
        }
    ],
    "commendations": ["Nice split."],
}

_ONE_NIT: Final[dict[str, object]] = {
    "findings": [
        {
            "path": "app/a.py",
            "line": 11,
            "severity": "nit",
            "rule": "8.3",
            "title": "Wording",
            "body": "Rename for clarity.",
        }
    ]
}


def test_publish_creates_the_sticky_and_one_batched_review(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    api = _install_api(
        monkeypatch,
        pages={_PULL_FILES: [{"filename": "app/a.py", "patch": _PATCH}]},
    )

    assert _publish(_write_report(tmp_path, _ONE_SUGGESTION)) == 0

    assert [(call.method, call.endpoint) for call in api.calls] == [
        ("POST", _ISSUE_COMMENTS),
        ("POST", _PULL_REVIEWS),
    ]
    review = api.calls[1].payload
    assert review["event"] == "COMMENT"
    comments = review["comments"]
    assert isinstance(comments, list)
    assert len(comments) == 1


def test_publish_updates_the_existing_sticky_in_place(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    api = _install_api(
        monkeypatch,
        pages={
            _ISSUE_COMMENTS: [{"id": 42, "user": {"type": "Bot"}, "body": f"old {STICKY_MARKER}"}],
            _PULL_FILES: [{"filename": "app/a.py", "patch": _PATCH}],
        },
    )

    assert _publish(_write_report(tmp_path, _ONE_SUGGESTION)) == 0

    assert api.calls[0].method == "PATCH"
    assert api.calls[0].endpoint == "repos/o/r/issues/comments/42"


def test_publish_skips_the_review_event_when_nothing_is_new(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    posted = _finding(severity=Severity.SUGGESTION, title="Problem").fingerprint
    api = _install_api(
        monkeypatch,
        pages={
            _PULL_FILES: [{"filename": "app/a.py", "patch": _PATCH}],
            _PULL_COMMENTS: [{"line": 11, "body": f"<!-- {FINGERPRINT_PREFIX}{posted} -->"}],
        },
    )

    assert _publish(_write_report(tmp_path, _ONE_SUGGESTION)) == 0

    assert [call.endpoint for call in api.calls] == [_ISSUE_COMMENTS]


def test_publish_keeps_a_nit_out_of_the_review_but_inside_the_sticky(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    api = _install_api(
        monkeypatch,
        pages={_PULL_FILES: [{"filename": "app/a.py", "patch": _PATCH}]},
    )

    assert _publish(_write_report(tmp_path, _ONE_NIT)) == 0

    assert [call.endpoint for call in api.calls] == [_ISSUE_COMMENTS]
    body = api.calls[0].payload["body"]
    assert isinstance(body, str)
    assert "Rename for clarity." in body


def test_publish_flags_the_sticky_as_stale_when_the_agent_wrote_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    api = _install_api(
        monkeypatch,
        pages={
            _ISSUE_COMMENTS: [{"id": 42, "user": {"type": "Bot"}, "body": f"old {STICKY_MARKER}"}]
        },
    )

    assert _publish(str(tmp_path / "absent.json")) == 0

    assert api.calls[0].method == "PATCH"
    body = api.calls[0].payload["body"]
    assert isinstance(body, str)
    assert STALE_MARKER in body


def test_publish_leaves_the_pull_request_untouched_without_a_sticky(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    api = _install_api(monkeypatch, pages={})

    assert _publish(str(tmp_path / "absent.json")) == 0
    assert api.calls == []


def test_publish_reports_invalid_agent_output_without_mutating(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    api = _install_api(monkeypatch, pages={})
    broken = tmp_path / "review.json"
    broken.write_text("{ not json", encoding="utf-8")

    assert _publish(str(broken)) == 1
    assert api.calls == []


def test_publish_dry_run_performs_no_mutation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    api = _install_api(
        monkeypatch,
        pages={_PULL_FILES: [{"filename": "app/a.py", "patch": _PATCH}]},
    )
    findings_path = _write_report(tmp_path, _ONE_SUGGESTION)

    assert (
        main(["publish", "--pr", "7", "--sha", "c0ffee1", "--findings", findings_path, "--dry-run"])
        == 0
    )

    assert api.calls == []
    assert "inline comments (1)" in capsys.readouterr().out


def test_load_previous_recovers_the_state_block(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    report = ReviewReport(findings=(_finding(severity=Severity.SUGGESTION),))
    sticky = f"{STICKY_MARKER}\nbody\n{encode_state(report, sha='c0ffee1')}"
    _install_api(
        monkeypatch,
        pages={_ISSUE_COMMENTS: [{"id": 1, "user": {"type": "Bot"}, "body": sticky}]},
    )
    out = tmp_path / "previous.json"

    assert main(["load-previous", "--pr", "7", "--out", str(out)]) == 0

    recovered = json.loads(out.read_text(encoding="utf-8"))
    assert [entry["title"] for entry in recovered] == ["Problem"]


@pytest.mark.parametrize(
    ("comment", "expected"),
    [
        pytest.param({"line": 11, "body": "<!-- fp -->"}, True, id="anchored_comment_counts"),
        pytest.param({"line": None, "body": "<!-- fp -->"}, False, id="outdated_comment_expires"),
        pytest.param({"body": "<!-- fp -->"}, False, id="absent_line_expires"),
        pytest.param({"line": 11, "body": "unrelated"}, False, id="comment_without_fingerprint"),
    ],
)
def test_posted_fingerprints_only_counts_visible_comments(
    monkeypatch: pytest.MonkeyPatch, comment: dict[str, object], expected: bool
) -> None:
    fingerprint = _finding().fingerprint
    body = comment["body"]
    assert isinstance(body, str)
    entry = dict(comment)
    entry["body"] = body.replace("fp", f"{FINGERPRINT_PREFIX}{fingerprint}")
    monkeypatch.setattr(github_api, "paginate", lambda endpoint: [entry])

    assert (fingerprint in _posted_fingerprints(repo="o/r", pr=1)) is expected
