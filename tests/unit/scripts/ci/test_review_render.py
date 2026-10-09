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

"""Unit tests for the Markdown surfaces produced for a pull request review."""

from __future__ import annotations

import pytest

from scripts.ci.review_render import (
    STALE_MARKER,
    apply_stale_banner,
    render_inline_body,
    render_review_body,
    render_sticky,
)
from scripts.ci.review_report import (
    FINGERPRINT_PREFIX,
    STICKY_MARKER,
    Finding,
    ReportDelta,
    ReviewReport,
    Severity,
    compute_delta,
    decode_state,
)

_REPO = "example-org/example-agent"
_SHA = "5f810a36b2292cd188b63364f03dbcd2590b8870"


def _finding(
    *,
    severity: Severity = Severity.BLOCKING,
    title: str = "Gap distribution drops the remainder",
    rule: str = "3.1",
) -> Finding:
    """Builds a finding with defaults suitable for rendering assertions."""
    return Finding(
        path="app/services/example_service.py",
        line=142,
        severity=severity,
        rule=rule,
        title=title,
        body="Explanation.",
    )


def _sticky(report: ReviewReport, *, delta: ReportDelta | None = None) -> str:
    """Renders a sticky summary with the fixed repository and commit under test."""
    return render_sticky(
        report,
        repo=_REPO,
        sha=_SHA,
        run_url="https://example.invalid/run/1",
        delta=delta if delta is not None else ReportDelta(),
    )


@pytest.mark.parametrize(
    ("severities", "expected_verdict", "expected_sections"),
    [
        pytest.param((), "✅ Approved", (), id="clean_review_has_no_finding_sections"),
        pytest.param(
            (Severity.BLOCKING,),
            "❌ Changes Requested",
            ("### 🔴 Blocking (1)",),
            id="blocking_is_rendered_expanded",
        ),
        pytest.param(
            (Severity.SUGGESTION, Severity.NIT),
            "✅ Approved with non-blocking suggestions",
            ("💡 Suggestions (1)", "🔹 Nits (1)"),
            id="non_blocking_still_approves_and_collapses",
        ),
    ],
)
def test_sticky_reflects_the_derived_verdict_and_sections(
    severities: tuple[Severity, ...],
    expected_verdict: str,
    expected_sections: tuple[str, ...],
) -> None:
    report = ReviewReport(
        findings=tuple(
            _finding(severity=severity, title=f"Finding {index}")
            for index, severity in enumerate(severities)
        )
    )
    body = _sticky(report)
    assert body.startswith(STICKY_MARKER)
    assert expected_verdict in body
    for section in expected_sections:
        assert section in body


def test_sticky_state_round_trips_through_the_rendered_body() -> None:
    report = ReviewReport(findings=(_finding(),))
    recovered = decode_state(_sticky(report))
    assert len(recovered) == 1
    assert recovered[0]["fp"] == report.findings[0].fingerprint


@pytest.mark.parametrize(
    "hostile_title",
    [
        pytest.param("Title with --> an early terminator", id="html_comment_terminator"),
        pytest.param("Title with <!-- a nested opener", id="nested_comment_opener"),
        pytest.param('Title with "quotes" and \\ backslash', id="json_metacharacters"),
    ],
)
def test_sticky_state_survives_hostile_titles(hostile_title: str) -> None:
    report = ReviewReport(findings=(_finding(title=hostile_title),))
    recovered = decode_state(_sticky(report))
    assert len(recovered) == 1
    assert recovered[0]["title"] == hostile_title


def test_sticky_renders_the_delta_against_the_previous_run() -> None:
    carried = _finding(title="Carried problem")
    resolved = _finding(title="Fixed problem", rule="7.1")
    delta = compute_delta(
        ReviewReport(findings=(carried,)),
        previous=(carried.to_state(), resolved.to_state()),
    )
    body = _sticky(ReviewReport(findings=(carried,)), delta=delta)
    assert "⏳ Carried over — §3.1 — Carried problem" in body
    assert "✅ Resolved — §7.1 — Fixed problem" in body


def test_inline_body_carries_severity_rule_and_fingerprint() -> None:
    finding = _finding()
    body = render_inline_body(finding)
    assert "**🔴 Blocking**" in body
    assert "`AGENTS.md §3.1`" in body
    assert f"<!-- {FINGERPRINT_PREFIX}{finding.fingerprint} -->" in body


def test_review_body_links_to_the_summary_instead_of_pointing_below() -> None:
    body = render_review_body(
        ReviewReport(findings=(_finding(),)),
        sha=_SHA,
        summary_url="https://github.com/o/r/pull/26#issuecomment-1",
    )
    assert "https://github.com/o/r/pull/26#issuecomment-1" in body
    assert "below" not in body.lower()
    assert _SHA[:7] in body


def test_stale_banner_is_inserted_once_and_replaced_on_reruns() -> None:
    original = _sticky(ReviewReport(findings=(_finding(),)))

    first = apply_stale_banner(original, sha="aaaaaaa1111")
    assert first.startswith(STICKY_MARKER)
    assert first.count(STALE_MARKER) == 1
    assert "aaaaaaa" in first

    second = apply_stale_banner(first, sha="bbbbbbb2222")
    assert second.count(STALE_MARKER) == 1
    assert "aaaaaaa" not in second
    assert "bbbbbbb" in second
    assert decode_state(second)[0]["rule"] == "3.1"


def test_sticky_carries_the_body_of_a_finding_never_posted_inline() -> None:
    """Nits are summary-only, so the summary must show their full explanation."""
    report = ReviewReport(
        findings=(
            Finding(
                path="app/a.py",
                line=11,
                severity=Severity.NIT,
                rule="8.3",
                title="Wording could be tighter",
                body="First paragraph.\n\n```suggestion\nresolved = list(items)\n```",
            ),
        )
    )

    sticky = render_sticky(report, repo="o/r", sha="c0ffee1", run_url="u", delta=ReportDelta())

    assert "  First paragraph." in sticky
    assert "  ```suggestion" in sticky
    assert "**Wording could be tighter**" in sticky
