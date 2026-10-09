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

"""Turn a fresh copy of the ADK starter template into a named project.

Usage:
    python scripts/init_project.py --name acme-invoice-agent [--display-name "ACME Invoice Agent"]

Rewrites the template's name tokens in a fixed set of files (including uv.lock, so no network
access or re-lock is needed) and removes template-only README sections. Running it again on an
initialized repository changes nothing.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

TEMPLATE_NAME: Final[str] = "adk-starter"
TEMPLATE_DISPLAY_NAME: Final[str] = "ADK Starter"

MAX_NAME_LENGTH: Final[int] = 26
"""agents-cli rejects project names longer than 26 characters."""

MAX_DISPLAY_NAME_LENGTH: Final[int] = 100

NAME_PATTERN: Final[re.Pattern[str]] = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")

FORBIDDEN_DISPLAY_NAME_CHARS: Final[frozenset[str]] = frozenset('"\\`\n\r')
"""Characters that would break the TOML, YAML and Python string literals being rewritten."""

TEMPLATE_BLOCK_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"[ \t]*<!-- template:begin -->.*?<!-- template:end -->[ \t]*\n?", re.DOTALL
)
TEMPLATE_BLOCK_FILES: Final[frozenset[str]] = frozenset({"README.md"})
_EXCESS_BLANK_LINES_PATTERN: Final[re.Pattern[str]] = re.compile(r"\n{3,}")

TARGET_FILES: Final[tuple[str, ...]] = (
    "pyproject.toml",
    "uv.lock",
    "agents-cli-manifest.yaml",
    "Dockerfile",
    "README.md",
    "AGENTS.md",
    "app/constants.py",
)
"""Files carrying the template identity. tests/unit/scripts/test_init_project.py fails when a
template token appears in any other file, or when a listed file no longer carries one, so this
list cannot silently go stale."""

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]


class InitError(ValueError):
    """Raised when the requested project name or display name is invalid."""


@dataclass(frozen=True)
class InitResult:
    """Outcome of an initialization run.

    Attributes:
        changed_files: Repository-relative paths whose content was (or would be) rewritten.
    """

    changed_files: tuple[str, ...]

    @property
    def already_initialized(self) -> bool:
        """Returns True when no file contained template tokens."""
        return not self.changed_files


def validate_name(name: str) -> str:
    """Validates a kebab-case project name.

    Args:
        name: Candidate project name, e.g. "acme-invoice-agent".

    Returns:
        The validated name.

    Raises:
        InitError: If the name is not kebab-case, too long, or the template's own name.
    """
    if len(name) > MAX_NAME_LENGTH:
        raise InitError(f"--name must be at most {MAX_NAME_LENGTH} characters (got {len(name)}).")
    if not NAME_PATTERN.fullmatch(name):
        raise InitError(
            f"--name must be kebab-case (lowercase letters, digits, single hyphens), got {name!r}."
        )
    if name == TEMPLATE_NAME:
        raise InitError("--name must differ from the template name.")
    return name


def default_display_name(name: str) -> str:
    """Derives a title-cased display name from a kebab-case project name.

    Args:
        name: Validated kebab-case project name.

    Returns:
        The display name, e.g. "Acme Invoice Agent" for "acme-invoice-agent".
    """
    return " ".join(part.capitalize() for part in name.split("-"))


def validate_display_name(display_name: str) -> str:
    """Validates a human-readable display name.

    Args:
        display_name: Candidate display name.

    Returns:
        The display name with surrounding whitespace removed.

    Raises:
        InitError: If the name is empty, too long, or contains characters that would break the
            rewritten string literals.
    """
    cleaned = display_name.strip()
    if not cleaned:
        raise InitError("--display-name must not be empty.")
    if len(cleaned) > MAX_DISPLAY_NAME_LENGTH:
        raise InitError(f"--display-name must be at most {MAX_DISPLAY_NAME_LENGTH} characters.")
    if FORBIDDEN_DISPLAY_NAME_CHARS & set(cleaned):
        raise InitError("--display-name must not contain quotes, backslashes or newlines.")
    return cleaned


_TOKEN_PATTERN: Final[re.Pattern[str]] = re.compile(
    f"{re.escape(TEMPLATE_DISPLAY_NAME)}|{re.escape(TEMPLATE_NAME)}"
)


def render(text: str, *, name: str, display_name: str, strip_template_blocks: bool) -> str:
    """Applies the template substitutions to one file's content.

    Args:
        text: Original file content.
        name: Validated project name replacing the template name.
        display_name: Validated display name replacing the template display name.
        strip_template_blocks: Whether to drop `<!-- template:begin/end -->` sections.

    Returns:
        The rewritten content.
    """
    if strip_template_blocks:
        text = _EXCESS_BLANK_LINES_PATTERN.sub("\n\n", TEMPLATE_BLOCK_PATTERN.sub("", text))
    replacements = {
        TEMPLATE_DISPLAY_NAME: display_name,
        TEMPLATE_NAME: name,
    }
    return _TOKEN_PATTERN.sub(lambda match: replacements[match.group(0)], text)


def initialize(root: Path, *, name: str, display_name: str, dry_run: bool = False) -> InitResult:
    """Rewrites the template identity in every target file under root.

    Args:
        root: Repository root.
        name: Validated project name.
        display_name: Validated display name.
        dry_run: When True, report the files that would change without writing them.

    Returns:
        The files that changed (or would change).
    """
    changed: list[str] = []
    for relative in TARGET_FILES:
        path = root / relative
        if not path.is_file():
            continue
        original = path.read_text(encoding="utf-8")
        updated = render(
            original,
            name=name,
            display_name=display_name,
            strip_template_blocks=relative in TEMPLATE_BLOCK_FILES,
        )
        if updated == original:
            continue
        changed.append(relative)
        if not dry_run:
            path.write_text(updated, encoding="utf-8")
    return InitResult(changed_files=tuple(changed))


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """Parses command-line arguments.

    Args:
        argv: Arguments without the program name; defaults to sys.argv[1:].

    Returns:
        The parsed arguments.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--name", required=True, help="kebab-case project name (<= 26 chars)")
    parser.add_argument("--display-name", help="human-readable name (default: from --name)")
    parser.add_argument("--dry-run", action="store_true", help="list changes without writing")
    parser.add_argument("--root", type=Path, default=_REPO_ROOT, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Runs the initializer.

    Args:
        argv: Arguments without the program name; defaults to sys.argv[1:].

    Returns:
        Process exit code: 0 on success, 2 on invalid input.
    """
    args = _parse_args(argv)
    raw_name: str = args.name
    raw_display_name: str | None = args.display_name
    root: Path = args.root
    dry_run: bool = args.dry_run
    try:
        name = validate_name(raw_name)
        display_name = validate_display_name(raw_display_name or default_display_name(name))
    except InitError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    result = initialize(root, name=name, display_name=display_name, dry_run=dry_run)
    if result.already_initialized:
        print("Nothing to do: this repository is already initialized.")
        return 0

    verb = "Would update" if dry_run else "Updated"
    for relative in result.changed_files:
        print(f"{verb} {relative}")
    if not dry_run:
        print(
            f"\nInitialized '{name}' ({display_name}). Next steps:\n"
            "  1. make install && make check\n"
            "  2. Fill in the project-specific rules in AGENTS.md §3.2\n"
            "  3. make bootstrap PROJECT_ID=<gcp-project> PREFIX=<3-16 char prefix>"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
