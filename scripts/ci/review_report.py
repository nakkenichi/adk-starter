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

"""Typed model and Markdown rendering for automated pull request review reports.

The review agent produces a plain JSON findings file; this module validates that
payload, derives the verdict deterministically from the findings themselves, and
renders the sticky summary comment and the batched review body.

Deriving the verdict here (rather than trusting the model's self-reported verdict)
is what prevents the contradictory states previously observed on pull requests,
where a summary claimed approval while a blocking review was still outstanding.
"""

from __future__ import annotations

import dataclasses
import enum
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Final

STATE_MARKER: Final[str] = "ai-review:state"
STICKY_MARKER: Final[str] = "<!-- ai-review:summary -->"
FINGERPRINT_PREFIX: Final[str] = "ai-review:fp:"

_STATE_RE: Final[re.Pattern[str]] = re.compile(
    rf"<!--\s*{re.escape(STATE_MARKER)}\s*(?P<payload>\{{[^\r\n]*\}})\s*-->",
)
_FINGERPRINT_LENGTH: Final[int] = 12


class ReviewReportError(ValueError):
    """Raised when the agent-produced findings payload is malformed."""


class Severity(enum.StrEnum):
    """Severity classification for a single review finding."""

    BLOCKING = "blocking"
    SUGGESTION = "suggestion"
    NIT = "nit"


class Verdict(enum.StrEnum):
    """Overall outcome of a review, derived from the set of findings.

    Approval turns solely on the absence of blocking findings. Non-blocking
    remarks never withhold approval, and no count threshold is applied: making
    approval depend on "few enough" suggestions would let ordinary variation in
    the number of remarks flip the verdict between otherwise identical runs.
    """

    APPROVED = "approved"
    APPROVED_WITH_SUGGESTIONS = "approved_with_suggestions"
    CHANGES_REQUESTED = "changes_requested"


SEVERITY_LABEL: Final[Mapping[Severity, str]] = {
    Severity.BLOCKING: "🔴 Blocking",
    Severity.SUGGESTION: "💡 Suggestion",
    Severity.NIT: "🔹 Nit",
}

VERDICT_LABEL: Final[Mapping[Verdict, str]] = {
    Verdict.APPROVED: "✅ Approved",
    Verdict.APPROVED_WITH_SUGGESTIONS: "✅ Approved with non-blocking suggestions",
    Verdict.CHANGES_REQUESTED: "❌ Changes Requested (advisory)",
}

# Only these severities are worth anchoring to a diff line; nits stay in the summary
# so the Files changed tab does not fill up with cosmetic remarks.
INLINE_SEVERITIES: Final[frozenset[Severity]] = frozenset({Severity.BLOCKING, Severity.SUGGESTION})


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class Finding:
    """A single review finding anchored to a file and line."""

    path: str
    line: int
    severity: Severity
    rule: str
    title: str
    body: str

    @property
    def fingerprint(self) -> str:
        """Stable identity of this finding across runs, ignoring line drift."""
        digest = hashlib.sha256(f"{self.path}|{self.rule}|{self.title}".encode())
        return digest.hexdigest()[:_FINGERPRINT_LENGTH]

    def to_state(self) -> dict[str, str]:
        """Serialises the identifying fields embedded in the sticky comment state."""
        return {
            "fp": self.fingerprint,
            "severity": str(self.severity),
            "rule": self.rule,
            "path": self.path,
            "title": self.title,
        }


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class ReviewReport:
    """A complete review outcome for one head commit."""

    findings: tuple[Finding, ...] = ()
    commendations: tuple[str, ...] = ()

    @property
    def verdict(self) -> Verdict:
        """Derives the verdict from the findings rather than trusting the model.

        The reviewer expresses its judgement by assigning severities, so a change
        it considers mergeable simply carries no blocking finding. Reading that
        signal here keeps the verdict impossible to contradict.
        """
        severities = {finding.severity for finding in self.findings}
        if Severity.BLOCKING in severities:
            return Verdict.CHANGES_REQUESTED
        if severities:
            return Verdict.APPROVED_WITH_SUGGESTIONS
        return Verdict.APPROVED

    def by_severity(self, severity: Severity) -> tuple[Finding, ...]:
        """Returns the findings matching ``severity`` in their original order."""
        return tuple(finding for finding in self.findings if finding.severity is severity)


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class ReportDelta:
    """Difference between the current findings and the previous run's findings."""

    new: tuple[Finding, ...] = ()
    carried: tuple[Finding, ...] = ()
    resolved: tuple[Mapping[str, str], ...] = ()


def _require_str(source: Mapping[str, object], key: str, *, context: str) -> str:
    """Extracts a non-empty string field or raises a descriptive error."""
    value = source.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ReviewReportError(f"{context}: field '{key}' must be a non-empty string")
    return value.strip()


def _require_line(source: Mapping[str, object], *, context: str) -> int:
    """Extracts a positive 1-based line number or raises a descriptive error."""
    value = source.get("line")
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ReviewReportError(f"{context}: field 'line' must be an integer >= 1")
    return value


def _parse_finding(raw: object, *, index: int) -> Finding:
    """Validates and converts one entry of the ``findings`` array."""
    context = f"findings[{index}]"
    if not isinstance(raw, dict):
        raise ReviewReportError(f"{context}: expected a JSON object")
    entry: Mapping[str, object] = {str(key): value for key, value in raw.items()}
    severity_raw = _require_str(entry, "severity", context=context).lower()
    try:
        severity = Severity(severity_raw)
    except ValueError as exc:
        allowed = ", ".join(str(item) for item in Severity)
        raise ReviewReportError(f"{context}: severity must be one of {allowed}") from exc
    return Finding(
        path=_require_str(entry, "path", context=context),
        line=_require_line(entry, context=context),
        severity=severity,
        rule=_require_str(entry, "rule", context=context),
        title=_require_str(entry, "title", context=context),
        body=_require_str(entry, "body", context=context),
    )


def parse_report(raw: object) -> ReviewReport:
    """Validates a decoded ``review.json`` payload into a typed report.

    Args:
        raw: The decoded JSON value written by the review agent.

    Returns:
        The validated report. The verdict is derived, never read from the payload.

    Raises:
        ReviewReportError: If the payload is not an object or a finding is malformed.
    """
    if not isinstance(raw, dict):
        raise ReviewReportError("review payload must be a JSON object")
    document: Mapping[str, object] = {str(key): value for key, value in raw.items()}

    findings_raw = document.get("findings", [])
    if not isinstance(findings_raw, list):
        raise ReviewReportError("field 'findings' must be a JSON array")
    findings = tuple(_parse_finding(item, index=index) for index, item in enumerate(findings_raw))

    commendations_raw = document.get("commendations", [])
    if not isinstance(commendations_raw, list):
        raise ReviewReportError("field 'commendations' must be a JSON array")
    commendations = tuple(
        item.strip() for item in commendations_raw if isinstance(item, str) and item.strip()
    )
    return ReviewReport(findings=findings, commendations=commendations)


def encode_state(report: ReviewReport, *, sha: str) -> str:
    """Renders the hidden HTML state block embedded in the sticky comment.

    Args:
        report: The report whose findings should be remembered for the next run.
        sha: The head commit the report was produced against.

    Returns:
        An HTML comment carrying a compact JSON snapshot of the findings.
    """
    payload = {
        "sha": sha,
        "findings": [finding.to_state() for finding in report.findings],
    }
    # A model-authored title may contain "-->", which would terminate the HTML
    # comment early and corrupt the state recovered by the next run. Escaping every
    # hyphen pair keeps the JSON semantically identical because "\u002d" decodes
    # back to "-", while making an early terminator impossible to express.
    encoded = json.dumps(payload, ensure_ascii=False).replace("--", "\\u002d\\u002d")
    return f"<!-- {STATE_MARKER} {encoded} -->"


def decode_state(body: str) -> tuple[Mapping[str, str], ...]:
    """Extracts the previous run's findings from a sticky comment body.

    The **last** match wins. The publisher always appends the state block at the
    very end, while a finding body rendered above it may legitimately quote the
    marker — which happens whenever the reviewer reviews this tooling. Reading
    the first match would decode that quotation as the previous state and report
    every carried-over finding as new.

    Args:
        body: The full Markdown body of a previously posted sticky comment.

    Returns:
        The previously recorded finding descriptors, or an empty tuple when the
        comment carries no parsable state block.
    """
    matches = _STATE_RE.findall(body)
    if not matches:
        return ()
    try:
        payload = json.loads(matches[-1])
    except json.JSONDecodeError:
        return ()
    if not isinstance(payload, dict):
        return ()
    entries = payload.get("findings")
    if not isinstance(entries, list):
        return ()
    recovered: list[Mapping[str, str]] = []
    for entry in entries:
        if isinstance(entry, dict):
            recovered.append({str(k): str(v) for k, v in entry.items()})
    return tuple(recovered)


def compute_delta(
    report: ReviewReport,
    *,
    previous: Sequence[Mapping[str, str]],
) -> ReportDelta:
    """Classifies findings as newly raised, carried over, or resolved since last run.

    Args:
        report: The current review report.
        previous: Finding descriptors recovered from the previous sticky comment.

    Returns:
        The categorised delta used to render the "changes since last review" section.
    """
    previous_by_fp = {entry.get("fp", ""): entry for entry in previous}
    current_fps = {finding.fingerprint for finding in report.findings}
    return ReportDelta(
        new=tuple(f for f in report.findings if f.fingerprint not in previous_by_fp),
        carried=tuple(f for f in report.findings if f.fingerprint in previous_by_fp),
        resolved=tuple(
            entry for fp, entry in previous_by_fp.items() if fp and fp not in current_fps
        ),
    )
