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

"""Tests for scripts/init_project.py (the `make init` template initializer)."""

from __future__ import annotations

from pathlib import Path
from typing import Final

import pytest

from scripts.init_project import (
    MAX_NAME_LENGTH,
    TARGET_FILES,
    TEMPLATE_DISPLAY_NAME,
    TEMPLATE_NAME,
    InitError,
    default_display_name,
    initialize,
    main,
    render,
    validate_display_name,
    validate_name,
)

_NAME: Final[str] = "acme-invoice-agent"
_DISPLAY_NAME: Final[str] = "ACME Invoice Agent"

# Directories never scanned for leftover template tokens (generated, vendored or cached).
_SKIPPED_DIRS: Final[frozenset[str]] = frozenset(
    {
        ".git",
        ".venv",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        ".terraform",
        ".google-agents-cli",
        "__pycache__",
        "artifacts",
        "scratch",
        "htmlcov",
    }
)
# Files that legitimately mention the tokens: the initializer itself and these tests.
_TOKEN_OWNERS: Final[frozenset[str]] = frozenset(
    {"scripts/init_project.py", "tests/unit/scripts/test_init_project.py"}
)


def _write_template_tree(root: Path) -> None:
    """Creates a minimal repository containing every kind of template token."""
    (root / "app").mkdir()
    (root / "pyproject.toml").write_text(f'[project]\nname = "{TEMPLATE_NAME}"\n', encoding="utf-8")
    (root / "app" / "constants.py").write_text(
        f'PROJECT_NAME = "{TEMPLATE_NAME}"\nPROJECT_DISPLAY_NAME = "{TEMPLATE_DISPLAY_NAME}"\n',
        encoding="utf-8",
    )
    (root / "README.md").write_text(
        f"# {TEMPLATE_DISPLAY_NAME}\n\n"
        "<!-- template:begin -->\nTemplate-only instructions.\n<!-- template:end -->\n\n"
        f"Run `{TEMPLATE_NAME}` locally.\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    "name",
    ["acme", "acme-invoice-agent", "a1-b2-c3", "x" * MAX_NAME_LENGTH],
)
def test_validate_name_accepts_kebab_case(name: str) -> None:
    assert validate_name(name) == name


@pytest.mark.parametrize(
    "name",
    [
        "",
        "Acme",
        "acme_invoice",
        "-acme",
        "acme-",
        "acme--agent",
        "1acme",
        "acme agent",
        "x" * (MAX_NAME_LENGTH + 1),
        TEMPLATE_NAME,
    ],
)
def test_validate_name_rejects_invalid_names(name: str) -> None:
    with pytest.raises(InitError):
        validate_name(name)


@pytest.mark.parametrize(
    ("name", "expected"),
    [("acme", "Acme"), ("acme-invoice-agent", "Acme Invoice Agent"), ("hr-faq2", "Hr Faq2")],
)
def test_default_display_name(name: str, expected: str) -> None:
    assert default_display_name(name) == expected


@pytest.mark.parametrize(
    ("display_name", "expected"),
    [
        ("ACME Invoice Agent", "ACME Invoice Agent"),
        ("  Padded  ", "Padded"),
        ("日本語 Agent", "日本語 Agent"),
    ],
)
def test_validate_display_name_accepts_valid_names(display_name: str, expected: str) -> None:
    assert validate_display_name(display_name) == expected


@pytest.mark.parametrize(
    "display_name",
    ["", "   ", 'Say "hi"', "back\\slash", "multi\nline", "x" * 101],
)
def test_validate_display_name_rejects_unsafe_names(display_name: str) -> None:
    with pytest.raises(InitError):
        validate_display_name(display_name)


@pytest.mark.parametrize(
    ("strip_template_blocks", "expected"),
    [
        (True, f"{_DISPLAY_NAME} / {_NAME}\nkeep\n"),
        (
            False,
            f"{_DISPLAY_NAME} / {_NAME}\n<!-- template:begin -->x<!-- template:end -->\nkeep\n",
        ),
    ],
)
def test_render(strip_template_blocks: bool, expected: str) -> None:
    text = (
        f"{TEMPLATE_DISPLAY_NAME} / {TEMPLATE_NAME}\n"
        "<!-- template:begin -->x<!-- template:end -->\nkeep\n"
    )

    rendered = render(
        text, name=_NAME, display_name=_DISPLAY_NAME, strip_template_blocks=strip_template_blocks
    )

    assert rendered == expected


def test_initialize_rewrites_targets_and_is_idempotent(tmp_path: Path) -> None:
    _write_template_tree(tmp_path)

    first = initialize(tmp_path, name=_NAME, display_name=_DISPLAY_NAME)
    second = initialize(tmp_path, name=_NAME, display_name=_DISPLAY_NAME)

    assert first.changed_files == ("pyproject.toml", "README.md", "app/constants.py")
    assert second.already_initialized
    readme = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert readme == f"# {_DISPLAY_NAME}\n\nRun `{_NAME}` locally.\n"
    constants = (tmp_path / "app" / "constants.py").read_text(encoding="utf-8")
    assert f'PROJECT_NAME = "{_NAME}"' in constants
    assert f'PROJECT_DISPLAY_NAME = "{_DISPLAY_NAME}"' in constants


def test_initialize_dry_run_writes_nothing(tmp_path: Path) -> None:
    _write_template_tree(tmp_path)
    before = (tmp_path / "pyproject.toml").read_text(encoding="utf-8")

    result = initialize(tmp_path, name=_NAME, display_name=_DISPLAY_NAME, dry_run=True)

    assert "pyproject.toml" in result.changed_files
    assert (tmp_path / "pyproject.toml").read_text(encoding="utf-8") == before


def test_main_initializes_and_reports(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_template_tree(tmp_path)

    exit_code = main(["--name", _NAME, "--root", str(tmp_path)])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Updated pyproject.toml" in output
    assert "Acme Invoice Agent" in (tmp_path / "README.md").read_text(encoding="utf-8")


def test_main_reports_already_initialized(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["--name", _NAME, "--root", str(tmp_path)])

    assert exit_code == 0
    assert "already initialized" in capsys.readouterr().out


@pytest.mark.parametrize(
    "argv",
    [["--name", "Bad_Name"], ["--name", _NAME, "--display-name", 'quote"d']],
)
def test_main_rejects_invalid_input(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    _write_template_tree(tmp_path)

    exit_code = main([*argv, "--root", str(tmp_path)])

    assert exit_code == 2
    assert capsys.readouterr().err.startswith("error:")
    assert TEMPLATE_NAME in (tmp_path / "pyproject.toml").read_text(encoding="utf-8")


def test_template_tokens_only_live_in_target_files(project_root: Path) -> None:
    offenders: list[str] = []
    for path in project_root.rglob("*"):
        relative = path.relative_to(project_root)
        if not path.is_file() or _SKIPPED_DIRS.intersection(relative.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        key = relative.as_posix()
        has_token = TEMPLATE_NAME in text or TEMPLATE_DISPLAY_NAME in text
        if has_token and key not in TARGET_FILES and key not in _TOKEN_OWNERS:
            offenders.append(key)

    assert offenders == [], f"Add these files to TARGET_FILES or drop the tokens: {offenders}"


def test_every_target_file_carries_a_template_token(project_root: Path) -> None:
    assert TEMPLATE_NAME == "adk-starter"
    assert TEMPLATE_DISPLAY_NAME == "ADK Starter"
    pyproject = (project_root / "pyproject.toml").read_text(encoding="utf-8")
    if f'name = "{TEMPLATE_NAME}"' not in pyproject:
        pytest.skip("Repository already initialized with `make init`.")

    stale: list[str] = []
    for relative in TARGET_FILES:
        text = (project_root / relative).read_text(encoding="utf-8")
        if TEMPLATE_NAME not in text and TEMPLATE_DISPLAY_NAME not in text:
            stale.append(relative)

    assert stale == [], f"Remove these token-free files from TARGET_FILES: {stale}"
