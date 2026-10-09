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

"""Unit tests for the review report model, state encoding and delta computation."""

from __future__ import annotations

import pytest

from scripts.ci.review_report import (
    Finding,
    ReviewReport,
    ReviewReportError,
    Severity,
    Verdict,
    compute_delta,
    decode_state,
    encode_state,
    parse_report,
)


def _finding(
    *,
    severity: Severity = Severity.SUGGESTION,
    rule: str = "3.1",
    title: str = "Gap distribution drops the remainder",
    path: str = "app/services/example_service.py",
    line: int = 142,
) -> Finding:
    """Builds a finding with sensible defaults for the cases under test."""
    return Finding(
        path=path,
        line=line,
        severity=severity,
        rule=rule,
        title=title,
        body="Explanation.",
    )


@pytest.mark.parametrize(
    ("severities", "expected"),
    [
        pytest.param((), Verdict.APPROVED, id="no_findings_is_approved"),
        pytest.param(
            (Severity.NIT,),
            Verdict.APPROVED_WITH_SUGGESTIONS,
            id="nit_only_still_approves",
        ),
        pytest.param(
            (Severity.SUGGESTION, Severity.NIT),
            Verdict.APPROVED_WITH_SUGGESTIONS,
            id="suggestions_and_nits_still_approve",
        ),
        pytest.param(
            tuple([Severity.SUGGESTION] * 12 + [Severity.NIT] * 9),
            Verdict.APPROVED_WITH_SUGGESTIONS,
            id="many_non_blocking_findings_still_approve",
        ),
        pytest.param(
            (Severity.NIT, Severity.BLOCKING),
            Verdict.CHANGES_REQUESTED,
            id="any_blocking_dominates",
        ),
    ],
)
def test_verdict_is_derived_from_severities(
    severities: tuple[Severity, ...], expected: Verdict
) -> None:
    report = ReviewReport(
        findings=tuple(
            _finding(severity=severity, title=f"Finding {index}")
            for index, severity in enumerate(severities)
        )
    )
    assert report.verdict is expected


@pytest.mark.parametrize(
    ("payload", "error_match"),
    [
        pytest.param([], "must be a JSON object", id="root_must_be_object"),
        pytest.param({"findings": {}}, "must be a JSON array", id="findings_must_be_array"),
        pytest.param({"findings": ["x"]}, "expected a JSON object", id="finding_must_be_object"),
        pytest.param(
            {"findings": [{"path": "a.py", "line": 1, "severity": "blocking", "rule": "1"}]},
            "field 'title' must be a non-empty string",
            id="missing_title",
        ),
        pytest.param(
            {
                "findings": [
                    {
                        "path": "a.py",
                        "line": 0,
                        "severity": "blocking",
                        "rule": "1",
                        "title": "t",
                        "body": "b",
                    }
                ]
            },
            "field 'line' must be an integer >= 1",
            id="line_must_be_positive",
        ),
        pytest.param(
            {
                "findings": [
                    {
                        "path": "a.py",
                        "line": 3,
                        "severity": "critical",
                        "rule": "1",
                        "title": "t",
                        "body": "b",
                    }
                ]
            },
            "severity must be one of",
            id="unknown_severity",
        ),
        pytest.param(
            {"findings": [], "commendations": "nope"},
            "'commendations' must be a JSON array",
            id="commendations_must_be_array",
        ),
    ],
)
def test_parse_report_rejects_malformed_payloads(payload: object, error_match: str) -> None:
    with pytest.raises(ReviewReportError, match=error_match):
        parse_report(payload)


@pytest.mark.parametrize(
    (
        "raw_path",
        "line",
        "severity_str",
        "rule",
        "title",
        "body",
        "expected_path",
    ),
    [
        pytest.param(
            " app/models/schema.py ",
            42,
            "BLOCKING",
            "2.4",
            "Raw str used for a discrete category",
            "Use StrEnum.",
            "app/models/schema.py",
            id="app_finding_trimmed_and_parsed",
        ),
        pytest.param(
            "terraform/iam.tf",
            18,
            "BLOCKING",
            "7.3",
            "Authoritative IAM binding used instead of member",
            "Use `google_project_iam_member` per §7.3.",
            "terraform/iam.tf",
            id="terraform_finding_parsed",
        ),
    ],
)
def test_parse_report_accepts_valid_findings_table(
    raw_path: str,
    line: int,
    severity_str: str,
    rule: str,
    title: str,
    body: str,
    expected_path: str,
) -> None:
    report = parse_report(
        {
            "findings": [
                {
                    "path": raw_path,
                    "line": line,
                    "severity": severity_str,
                    "rule": rule,
                    "title": title,
                    "body": body,
                }
            ],
            "commendations": ["Nice tests.", "  ", 7],
        }
    )
    assert report.verdict is Verdict.CHANGES_REQUESTED
    assert report.findings[0].severity is Severity.BLOCKING
    finding = report.findings[0]
    assert (finding.path, finding.line, finding.rule, finding.title, finding.body) == (
        expected_path,
        line,
        rule,
        title,
        body,
    )
    assert report.commendations == ("Nice tests.",)


def test_fingerprint_ignores_line_drift_but_tracks_identity() -> None:
    moved = _finding(line=999)
    same = _finding(line=142)
    renamed = _finding(title="A different problem")
    assert moved.fingerprint == same.fingerprint
    assert renamed.fingerprint != same.fingerprint


@pytest.mark.parametrize(
    ("body", "expected_count"),
    [
        pytest.param("no marker here", 0, id="absent_marker"),
        pytest.param("<!-- ai-review:state {oops -->", 0, id="malformed_json"),
        pytest.param('<!-- ai-review:state {"findings": 3} -->', 0, id="wrong_shape"),
        pytest.param('<!-- ai-review:state {"findings": []} -->', 0, id="empty_findings"),
    ],
)
def test_decode_state_degrades_gracefully(body: str, expected_count: int) -> None:
    assert len(decode_state(body)) == expected_count


def test_state_survives_an_encode_decode_round_trip() -> None:
    report = ReviewReport(findings=(_finding(severity=Severity.BLOCKING),))
    recovered = decode_state(f"## Summary\n\n{encode_state(report, sha='abc1234')}")
    assert len(recovered) == 1
    assert recovered[0]["fp"] == report.findings[0].fingerprint
    assert recovered[0]["rule"] == "3.1"


def test_compute_delta_classifies_new_carried_and_resolved() -> None:
    carried = _finding(rule="3.1", title="Carried problem")
    fresh = _finding(rule="2.11", title="Brand new problem")
    gone = _finding(rule="7.1", title="Fixed problem")
    previous = (carried.to_state(), gone.to_state())

    delta = compute_delta(ReviewReport(findings=(carried, fresh)), previous=previous)

    assert tuple(f.title for f in delta.new) == ("Brand new problem",)
    assert tuple(f.title for f in delta.carried) == ("Carried problem",)
    assert tuple(entry["title"] for entry in delta.resolved) == ("Fixed problem",)


def test_state_block_quoted_in_a_finding_body_does_not_win() -> None:
    """The reviewer reviews this tooling, so a body may legitimately quote the marker.

    The genuine block is always appended last, so the last match must be the one
    decoded. Reading the first one reported every carried-over finding as new.
    """
    report = ReviewReport(
        findings=(
            Finding(
                path="scripts/ci/review_report.py",
                line=240,
                severity=Severity.SUGGESTION,
                rule="3.1",
                title="The first state block wins",
                body=(
                    "A finding body containing "
                    '<!-- ai-review:state {"sha": "forged", "findings": []} -->'
                    " would be decoded as the previous state."
                ),
            ),
        )
    )
    sticky = f"## Summary\n\n- {report.findings[0].body}\n\n{encode_state(report, sha='abc1234')}"

    recovered = decode_state(sticky)

    assert [entry["title"] for entry in recovered] == ["The first state block wins"]
