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

"""Folder architecture and layering guards.

These checks parse the source with `ast`, so they stay fast, offline and independent of import
side effects. When one fails, move the code to the right layer instead of relaxing the rule.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

import pytest

_PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
_APP_DIR: Final[Path] = _PROJECT_ROOT / "app"

_SERVING_LAYER: Final[tuple[str, ...]] = ("app.app_utils", "app.fast_api_app", "app.agent")
"""Composition root and serving glue: imported by nothing below them."""

_FORBIDDEN_IMPORTS: Final[dict[str, tuple[str, ...]]] = {
    # Pure domain layer: schemas, enums and exceptions only.
    "models": (
        "google.adk",
        "app.agents",
        "app.tools",
        "app.services",
        "app.plugins",
        *_SERVING_LAYER,
    ),
    # Business logic and Google Cloud clients, reusable outside ADK.
    "services": ("google.adk", "app.agents", "app.tools", "app.plugins", *_SERVING_LAYER),
    # Thin adapters over services: never depend on agent factories or plugins.
    "tools": ("app.agents", "app.plugins", *_SERVING_LAYER),
    "plugins": ("app.agents", "app.tools", "app.services", *_SERVING_LAYER),
    "agents": _SERVING_LAYER,
}
_ALLOWED_IMPORTS: Final[dict[str, tuple[str, ...]]] = {
    "tools": ("app.agents.state",),
    "plugins": ("app.agents.state", "app.services"),
}
"""Exceptions to _FORBIDDEN_IMPORTS: tools and plugins use canonical state keys and services."""

_MODEL_CONSTANTS: Final[Path] = _APP_DIR / "agents" / "constants.py"
_PROMPT_KEYWORDS: Final[frozenset[str]] = frozenset({"instruction", "description"})


def _python_files(directory: Path) -> list[Path]:
    return sorted(path for path in directory.rglob("*.py") if "__pycache__" not in path.parts)


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imported_modules(tree: ast.Module) -> list[str]:
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.append(node.module)
    return modules


def _is_within(module: str, package: str) -> bool:
    return module == package or module.startswith(f"{package}.")


def _relative(path: Path) -> str:
    return path.relative_to(_PROJECT_ROOT).as_posix()


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("import google.adk.tools", ["google.adk.tools"], id="import"),
        pytest.param("from google.adk import Agent", ["google.adk"], id="from-import"),
        pytest.param("def f():\n    import app.agent\n", ["app.agent"], id="nested-import"),
    ],
)
def test_import_parser_sees_every_import_form(source: str, expected: list[str]) -> None:
    assert _imported_modules(ast.parse(source)) == expected


@pytest.mark.parametrize(
    ("module", "package", "expected"),
    [
        pytest.param("app.agents", "app.agents", True, id="same"),
        pytest.param("app.agents.state", "app.agents", True, id="child"),
        pytest.param("app.agents_extra", "app.agents", False, id="prefix-only"),
        pytest.param("app.agents.state", "app.agent", False, id="sibling"),
    ],
)
def test_is_within(module: str, package: str, expected: bool) -> None:
    assert _is_within(module, package) is expected


@pytest.mark.parametrize("layer", sorted(_FORBIDDEN_IMPORTS))
def test_layers_only_import_downwards(layer: str) -> None:
    forbidden = _FORBIDDEN_IMPORTS[layer]
    allowed = _ALLOWED_IMPORTS.get(layer, ())
    violations = [
        f"{_relative(path)} imports {module}"
        for path in _python_files(_APP_DIR / layer)
        for module in _imported_modules(_parse(path))
        if any(_is_within(module, package) for package in forbidden)
        and not any(_is_within(module, package) for package in allowed)
    ]

    assert violations == []


def test_model_ids_live_in_agents_constants() -> None:
    violations = [
        f"{_relative(path)}:{node.lineno} {node.value!r}"
        for path in _python_files(_APP_DIR)
        if path != _MODEL_CONSTANTS
        for node in ast.walk(_parse(path))
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.startswith("gemini-")
    ]

    assert violations == [], "Define model ids in app/agents/constants.py"


def test_agent_prompts_live_in_prompts_modules() -> None:
    violations = [
        f"{_relative(path)}:{keyword.value.lineno} {keyword.arg}="
        for path in _python_files(_APP_DIR / "agents")
        if path.name != "prompts.py"
        for node in ast.walk(_parse(path))
        if isinstance(node, ast.Call)
        for keyword in node.keywords
        if keyword.arg in _PROMPT_KEYWORDS
        and isinstance(keyword.value, (ast.Constant, ast.JoinedStr))
    ]

    assert violations == [], "Move instruction/description text into the agent's prompts.py"


def test_session_state_keys_are_named_constants() -> None:
    violations = [
        f"{_relative(path)}:{node.lineno}"
        for path in _python_files(_APP_DIR)
        for node in ast.walk(_parse(path))
        if isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "state"
        and isinstance(node.slice, ast.Constant)
    ]

    assert violations == [], "Use the Final keys defined in app/agents/state.py"


def test_every_agent_package_has_factory_and_prompts() -> None:
    packages = [path.parent for path in (_APP_DIR / "agents").glob("*/__init__.py")]

    assert packages, "app/agents/ must contain at least one agent package"
    for package in packages:
        assert (package / "agent.py").is_file(), _relative(package)
        assert (package / "prompts.py").is_file(), _relative(package)
