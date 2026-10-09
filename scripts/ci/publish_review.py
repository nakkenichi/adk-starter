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

"""Reflects an agent-produced review onto a GitHub pull request, idempotently.

Run as a module from the repository root so that the ``scripts`` package resolves::

    python -m scripts.ci.publish_review load-previous --pr 19 --out previous.json
    python -m scripts.ci.publish_review publish --pr 19 --sha "$SHA" \
        --findings review.json --run-url "$RUN_URL"

All GitHub mutations happen in the single ``publish`` invocation, so a cancelled or
timed-out review agent can never leave a pull request in a half-updated state.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final

from scripts.ci import github_api
from scripts.ci.review_render import (
    apply_stale_banner,
    render_inline_body,
    render_review_body,
    render_sticky,
)
from scripts.ci.review_report import (
    FINGERPRINT_PREFIX,
    INLINE_SEVERITIES,
    STICKY_MARKER,
    Finding,
    ReviewReport,
    ReviewReportError,
    compute_delta,
    decode_state,
    parse_report,
)

logger: Final = logging.getLogger("publish_review")

_HUNK_RE: Final[re.Pattern[str]] = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_FINGERPRINT_RE: Final[re.Pattern[str]] = re.compile(
    rf"<!--\s*{re.escape(FINGERPRINT_PREFIX)}([0-9a-f]+)\s*-->"
)

# Only a bot account may own the sticky summary; see _find_sticky.
_BOT_ACCOUNT_TYPE: Final[str] = "Bot"


class PublishError(RuntimeError):
    """Raised when the publishing step cannot complete."""


def _repo_slug() -> str:
    """Reads the ``owner/name`` slug from the Actions environment."""
    slug = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not slug:
        raise PublishError("GITHUB_REPOSITORY is not set")
    return slug


def _commentable_lines(patch: str) -> set[int]:
    """Returns the new-file line numbers that GitHub accepts comments on.

    Args:
        patch: The unified diff hunk text for a single file.

    Returns:
        Every line number present on the right-hand side of the diff. Added and
        context lines qualify; removed lines do not advance the new-file cursor.
    """
    lines: set[int] = set()
    cursor = 0
    for raw in patch.splitlines():
        hunk = _HUNK_RE.match(raw)
        if hunk is not None:
            cursor = int(hunk.group(1))
            continue
        if cursor == 0 or raw.startswith("\\"):
            continue
        if raw.startswith(("+", " ")):
            lines.add(cursor)
            cursor += 1
    return lines


def _diff_index(*, repo: str, pr: int) -> dict[str, set[int]]:
    """Maps each changed file to the set of lines that can carry a comment.

    Without this guard a single out-of-range line makes the whole batched
    ``POST /pulls/{n}/reviews`` call fail with HTTP 422.
    """
    index: dict[str, set[int]] = {}
    for item in github_api.paginate(f"repos/{repo}/pulls/{pr}/files"):
        entry = github_api.as_mapping(item)
        filename = github_api.field_str(entry, "filename")
        patch = github_api.field_str(entry, "patch")
        if filename and patch:
            index[filename] = _commentable_lines(patch)
    return index


def _posted_fingerprints(*, repo: str, pr: int) -> set[str]:
    """Collects the fingerprints of findings whose inline comment is still visible.

    A comment whose line has drifted since it was written is returned with a null
    ``line`` and is collapsed out of the Files changed tab. Counting it as posted
    would suppress the comment forever, so an unresolved finding could never
    regain an anchor and a regression would be reported with no comment at all.
    """
    seen: set[str] = set()
    for item in github_api.paginate(f"repos/{repo}/pulls/{pr}/comments"):
        entry = github_api.as_mapping(item)
        if "user" in entry:
            author = entry.get("user")
            if not isinstance(author, dict):
                continue
            if github_api.field_str(github_api.as_mapping(author), "type") != _BOT_ACCOUNT_TYPE:
                continue
        if github_api.field_int(entry, "line") <= 0:
            continue
        match = _FINGERPRINT_RE.search(github_api.field_str(entry, "body"))
        if match is not None:
            seen.add(match.group(1))
    return seen


def _find_sticky(*, repo: str, pr: int) -> Mapping[str, object] | None:
    """Locates the sticky summary comment by its hidden marker.

    Matching on a marker rather than on "the bot's most recent comment" keeps the
    update stable even when other bots comment in between.

    The author is checked as well, because issue comments on a pull request can be
    written by anyone with read access. An unauthenticated marker match would let
    an outsider plant a forged state block that `load-previous` feeds into the
    reviewer's prompt, and would redirect every later update onto their comment.
    """
    for item in github_api.paginate(f"repos/{repo}/issues/{pr}/comments"):
        entry = github_api.as_mapping(item)
        author = entry.get("user")
        if not isinstance(author, dict):
            continue
        if github_api.field_str(github_api.as_mapping(author), "type") != _BOT_ACCOUNT_TYPE:
            continue
        if STICKY_MARKER in github_api.field_str(entry, "body"):
            return entry
    return None


def _upsert_sticky(*, repo: str, pr: int, body: str, existing_id: int) -> str:
    """Creates or updates the sticky summary comment and returns its permalink."""
    if existing_id:
        response = github_api.request(
            "PATCH",
            f"repos/{repo}/issues/comments/{existing_id}",
            payload={"body": body},
        )
    else:
        response = github_api.request(
            "POST",
            f"repos/{repo}/issues/{pr}/comments",
            payload={"body": body},
        )
    return github_api.field_str(github_api.as_mapping(response), "html_url")


def _inline_payloads(
    findings: Sequence[Finding],
    *,
    diff_index: Mapping[str, set[int]],
    already_posted: set[str],
) -> list[dict[str, object]]:
    """Builds the ``comments[]`` array for the batched review submission.

    Findings are dropped when their severity is summary-only, when their fingerprint
    was already posted on an earlier run, or when their line is not part of the diff.
    """
    payloads: list[dict[str, object]] = []
    for finding in findings:
        if finding.severity not in INLINE_SEVERITIES:
            continue
        if finding.fingerprint in already_posted:
            logger.info("Skipping already posted finding %s", finding.fingerprint)
            continue
        if finding.line not in diff_index.get(finding.path, set()):
            logger.warning(
                "Dropping %s:%d — not a commentable line in this diff",
                finding.path,
                finding.line,
            )
            continue
        payloads.append(
            {
                "path": finding.path,
                "line": finding.line,
                "side": "RIGHT",
                "body": render_inline_body(finding),
            }
        )
    return payloads


def _load_report(findings_path: Path) -> ReviewReport | None:
    """Reads and validates the agent output, returning ``None`` when absent.

    A missing file means the agent was cancelled or timed out before writing its
    result. In that case the pull request is deliberately left untouched.
    """
    if not findings_path.is_file():
        logger.warning("No findings file at %s — leaving the pull request untouched", findings_path)
        return None
    try:
        raw = json.loads(findings_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PublishError(f"{findings_path} is not valid JSON: {exc}") from exc
    try:
        return parse_report(raw)
    except ReviewReportError as exc:
        raise PublishError(f"{findings_path} failed validation: {exc}") from exc


def _command_load_previous(args: argparse.Namespace) -> int:
    """Writes the previous run's findings so they can be injected into the prompt."""
    repo = _repo_slug()
    sticky = _find_sticky(repo=repo, pr=args.pr)
    previous = decode_state(github_api.field_str(sticky, "body")) if sticky else ()
    Path(args.out).write_text(json.dumps(previous, ensure_ascii=False), encoding="utf-8")
    logger.info("Recovered %d finding(s) from the previous review", len(previous))
    return 0


def _mark_sticky_stale(*, repo: str, pr: int, sha: str) -> int:
    """Flags the existing summary as describing an older commit.

    Called when the agent produced no ``review.json``. The findings are kept, but
    the reader is told that the run for the current head commit never finished.
    """
    sticky = _find_sticky(repo=repo, pr=pr)
    if sticky is None:
        logger.info("No sticky summary to flag as stale")
        return 0
    body = github_api.field_str(sticky, "body")
    updated = apply_stale_banner(body, sha=sha)
    if updated != body:
        github_api.request(
            "PATCH",
            f"repos/{repo}/issues/comments/{github_api.field_int(sticky, 'id')}",
            payload={"body": updated},
        )
        logger.info("Flagged the existing summary as stale for %s", sha[:7])
    return 0


def _command_publish(args: argparse.Namespace) -> int:
    """Reflects the agent's findings onto the pull request in a single pass."""
    repo = _repo_slug()
    report = _load_report(Path(args.findings))
    if report is None:
        return _mark_sticky_stale(repo=repo, pr=args.pr, sha=args.sha)

    sticky = _find_sticky(repo=repo, pr=args.pr)
    previous = decode_state(github_api.field_str(sticky, "body")) if sticky else ()
    delta = compute_delta(report, previous=previous)
    sticky_body = render_sticky(
        report,
        repo=repo,
        sha=args.sha,
        run_url=args.run_url,
        delta=delta,
    )

    diff_index = _diff_index(repo=repo, pr=args.pr)
    already_posted = _posted_fingerprints(repo=repo, pr=args.pr)
    comments = _inline_payloads(
        report.findings,
        diff_index=diff_index,
        already_posted=already_posted,
    )

    if args.dry_run:
        sys.stdout.write(f"{sticky_body}\n\n----- inline comments ({len(comments)}) -----\n")
        sys.stdout.write(json.dumps(comments, ensure_ascii=False, indent=2))
        sys.stdout.write("\n")
        return 0

    sticky_id = github_api.field_int(sticky, "id") if sticky else 0
    summary_url = _upsert_sticky(repo=repo, pr=args.pr, body=sticky_body, existing_id=sticky_id)
    logger.info("Sticky summary available at %s", summary_url)

    if not comments:
        # Either the change is clean, every finding is summary-only, or all of them
        # were posted by an earlier run. In each case a review event would carry no
        # information the sticky summary does not already show.
        logger.info("No new inline comments — the sticky summary alone is up to date")
        return 0

    github_api.request(
        "POST",
        f"repos/{repo}/pulls/{args.pr}/reviews",
        payload={
            "commit_id": args.sha,
            "body": render_review_body(report, sha=args.sha, summary_url=summary_url),
            "event": "COMMENT",
            "comments": comments,
        },
    )
    logger.info(
        "Submitted one review with %d inline comment(s) for %d finding(s)",
        len(comments),
        len(report.findings),
    )
    return 0


def _build_parser() -> argparse.ArgumentParser:
    """Defines the command line interface."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    previous = subparsers.add_parser(
        "load-previous",
        help="Recover the previous run's findings from the sticky comment",
    )
    previous.add_argument("--pr", type=int, required=True)
    previous.add_argument("--out", required=True)
    previous.set_defaults(handler=_command_load_previous)

    publish = subparsers.add_parser(
        "publish",
        help="Publish the findings as one sticky comment plus one batched review",
    )
    publish.add_argument("--pr", type=int, required=True)
    publish.add_argument("--sha", required=True)
    publish.add_argument("--findings", required=True)
    publish.add_argument("--run-url", default="")
    publish.add_argument("--dry-run", action="store_true")
    publish.set_defaults(handler=_command_publish)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parses arguments and dispatches to the selected sub-command.

    Args:
        argv: Command line arguments; defaults to ``sys.argv[1:]``.

    Returns:
        The process exit status.
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = _build_parser().parse_args(argv)
    try:
        result: int = args.handler(args)
    except (PublishError, github_api.GitHubApiError) as exc:
        logger.error("%s", exc)
        return 1
    return result


if __name__ == "__main__":
    raise SystemExit(main())
