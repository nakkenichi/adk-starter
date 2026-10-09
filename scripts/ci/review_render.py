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

"""Markdown rendering for the sticky summary, batched review body and inline comments.

Three surfaces are produced, each with one job:

* the sticky comment carries the full summary and is updated in place every run,
* the batched review body carries a one-line verdict plus a real permalink to the
  sticky comment (never a "see the comment below" cross-reference, which is wrong
  because an edited comment keeps its original timeline position),
* the inline comment bodies carry the individual findings.
"""

from __future__ import annotations

import re
import textwrap
from collections.abc import Sequence
from typing import Final

from scripts.ci.review_report import (
    FINGERPRINT_PREFIX,
    SEVERITY_LABEL,
    STICKY_MARKER,
    VERDICT_LABEL,
    Finding,
    ReportDelta,
    ReviewReport,
    Severity,
    encode_state,
)

STALE_MARKER: Final[str] = "<!-- ai-review:stale -->"

# Continuation lines of a list item need two spaces so that a multi-line body,
# including fenced code, stays inside the bullet rather than closing it.
_BODY_INDENT: Final[str] = "  "

# Matches the marker plus the blockquote lines that follow it, so that repeated
# incomplete runs replace the banner instead of stacking one per failure.
_STALE_BANNER_RE: Final[re.Pattern[str]] = re.compile(
    rf"{re.escape(STALE_MARKER)}\n(?:>.*\n)*\n?",
)

_ADVISORY_NOTE: Final[str] = (
    "> Lint, typing and tests are gated deterministically by `CI / Fast Offline CI`.\n"
    "> This review is an advisory second opinion and never blocks merging."
)


def _blob_url(*, repo: str, sha: str, finding: Finding) -> str:
    """Builds a permalink to the reviewed line at the exact head commit."""
    return f"https://github.com/{repo}/blob/{sha}/{finding.path}#L{finding.line}"


def _counts_line(report: ReviewReport) -> str:
    """Renders the compact per-severity tally shown in every surface."""
    parts = [
        f"🔴 {len(report.by_severity(Severity.BLOCKING))} blocking",
        f"💡 {len(report.by_severity(Severity.SUGGESTION))} suggestions",
        f"🔹 {len(report.by_severity(Severity.NIT))} nits",
    ]
    return " · ".join(parts)


def render_inline_body(finding: Finding) -> str:
    """Renders the body of a single inline review comment.

    Args:
        finding: The finding to describe.

    Returns:
        Markdown carrying the severity, the cited rule, and a hidden fingerprint
        used to skip re-posting the same finding on a later run.
    """
    label = SEVERITY_LABEL[finding.severity]
    return (
        f"**{label}** · `AGENTS.md §{finding.rule}`\n\n"
        f"**{finding.title}**\n\n"
        f"{finding.body}\n\n"
        f"<!-- {FINGERPRINT_PREFIX}{finding.fingerprint} -->"
    )


def _render_finding_list(
    findings: Sequence[Finding],
    *,
    repo: str,
    sha: str,
) -> str:
    """Renders findings as permalinked entries carrying their full explanation.

    The body is included because a finding is not guaranteed to be readable
    anywhere else: nits are never posted inline, a finding whose line falls
    outside the diff is dropped, and one already posted on an earlier run is not
    repeated. The sticky summary is therefore the only complete surface.
    """
    if not findings:
        return "_None._"
    entries = [
        f"- [`{f.path}:{f.line}`]({_blob_url(repo=repo, sha=sha, finding=f)})"
        f" — §{f.rule} — **{f.title}**\n\n"
        f"{textwrap.indent(f.body, _BODY_INDENT)}"
        for f in findings
    ]
    return "\n\n".join(entries)


def _render_delta(delta: ReportDelta) -> str:
    """Renders the new / carried / resolved breakdown against the previous run."""
    sections: list[str] = []
    if delta.resolved:
        resolved = "\n".join(
            f"- ✅ Resolved — §{entry.get('rule', '?')} — {entry.get('title', '')}"
            for entry in delta.resolved
        )
        sections.append(resolved)
    if delta.new:
        sections.append("\n".join(f"- ➕ New — §{f.rule} — {f.title}" for f in delta.new))
    if delta.carried:
        sections.append(
            "\n".join(f"- ⏳ Carried over — §{f.rule} — {f.title}" for f in delta.carried)
        )
    if not sections:
        return "_No change since the previous review._"
    return "\n".join(sections)


def render_sticky(
    report: ReviewReport,
    *,
    repo: str,
    sha: str,
    run_url: str,
    delta: ReportDelta,
) -> str:
    """Renders the full sticky summary comment.

    Args:
        report: The current review report.
        repo: The ``owner/name`` slug used to build permalinks.
        sha: The reviewed head commit.
        run_url: Link to the workflow run that produced the review.
        delta: The difference against the previous run's findings.

    Returns:
        The complete Markdown body, including the hidden state block consumed by
        the next run.
    """
    blocking = report.by_severity(Severity.BLOCKING)
    suggestions = report.by_severity(Severity.SUGGESTION)
    nits = report.by_severity(Severity.NIT)
    short_sha = sha[:7]

    parts: list[str] = [
        STICKY_MARKER,
        "## 🤖 Automated Code Review",
        "",
        "| | |",
        "|---|---|",
        f"| **Verdict** | {VERDICT_LABEL[report.verdict]} |",
        f"| **Reviewed** | [`{short_sha}`](https://github.com/{repo}/commit/{sha}) |",
        f"| **Findings** | {_counts_line(report)} |",
        f"| **Run** | [workflow logs]({run_url}) |",
        "",
        _ADVISORY_NOTE,
        "",
    ]

    if blocking:
        parts += [
            f"### 🔴 Blocking ({len(blocking)})",
            "",
            _render_finding_list(blocking, repo=repo, sha=sha),
            "",
        ]

    if suggestions:
        parts += [
            f"<details><summary>💡 Suggestions ({len(suggestions)})</summary>",
            "",
            _render_finding_list(suggestions, repo=repo, sha=sha),
            "",
            "</details>",
            "",
        ]

    if nits:
        parts += [
            f"<details><summary>🔹 Nits ({len(nits)})</summary>",
            "",
            _render_finding_list(nits, repo=repo, sha=sha),
            "",
            "</details>",
            "",
        ]

    parts += [
        "<details><summary>🔁 Changes since the previous review</summary>",
        "",
        _render_delta(delta),
        "",
        "</details>",
        "",
    ]

    if report.commendations:
        parts += [
            "<details><summary>👍 Commendations</summary>",
            "",
            "\n".join(f"- {item}" for item in report.commendations),
            "",
            "</details>",
            "",
        ]

    parts.append(encode_state(report, sha=sha))
    return "\n".join(parts)


def render_review_body(report: ReviewReport, *, sha: str, summary_url: str) -> str:
    """Renders the short body of the single batched review.

    Args:
        report: The current review report.
        sha: The reviewed head commit.
        summary_url: Direct permalink to the sticky summary comment.

    Returns:
        A compact Markdown body that links to the summary instead of describing
        its position in the timeline.
    """
    return (
        f"### 🤖 Automated Code Review — {VERDICT_LABEL[report.verdict]}\n\n"
        f"{_counts_line(report)} · reviewed `{sha[:7]}`\n\n"
        f"📋 **[Full summary and changes since the previous review]({summary_url})**\n\n"
        f"{_ADVISORY_NOTE}"
    )


def apply_stale_banner(body: str, *, sha: str) -> str:
    """Marks an existing sticky summary as describing an older commit.

    When the agent is cancelled or times out there is no ``review.json``, so the
    findings are left untouched. Without this banner the previous summary would
    still read as though it described the current head commit.

    Args:
        body: The current sticky comment body.
        sha: The head commit whose review run did not complete.

    Returns:
        The body with exactly one banner, replacing any banner from an earlier
        incomplete run so that repeated failures do not stack notices.
    """
    without_previous = _STALE_BANNER_RE.sub("", body)
    banner = (
        f"{STALE_MARKER}\n"
        "> [!WARNING]\n"
        f"> The review of `{sha[:7]}` did not complete. "
        "The summary below describes an earlier commit.\n"
    )
    if without_previous.startswith(STICKY_MARKER):
        remainder = without_previous[len(STICKY_MARKER) :].lstrip("\n")
        return f"{STICKY_MARKER}\n{banner}\n{remainder}"
    return f"{banner}\n{without_previous}"
