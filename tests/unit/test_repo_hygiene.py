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

"""Repository hygiene guards for the build, packaging and dependency configuration."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Final

import pathspec
import pytest
import yaml

PYPI_SIMPLE: Final[str] = "https://pypi.org/simple"
_REGISTRY: Final[re.Pattern[str]] = re.compile(r'registry = "([^"]+)"')
_IGNORE_FILES: Final[tuple[str, ...]] = (".dockerignore", ".gcloudignore")
# Must never ship: secrets, local state, and code that is not part of the runtime image.
_MUST_EXCLUDE: Final[tuple[str, ...]] = (
    ".env",
    ".env.local",
    ".coverage",
    ".git/",
    ".venv/",
    ".google-agents-cli/",
    "artifacts/",
    "tests/",
    "terraform/",
    "scripts/",
    "deployment_metadata.json",
)
# Must ship: everything the Dockerfile copies (Agent Runtime builds from this upload).
_MUST_INCLUDE: Final[tuple[str, ...]] = (
    "Dockerfile",
    "pyproject.toml",
    "uv.lock",
    "README.md",
    "app/agent.py",
    "app/fast_api_app.py",
)


def _ignore_spec(path: Path) -> pathspec.GitIgnoreSpec:
    return pathspec.GitIgnoreSpec.from_lines(path.read_text(encoding="utf-8").splitlines())


def _patterns(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_uv_lock_only_uses_public_pypi(project_root: Path) -> None:
    # A private or mirror package index in uv.lock breaks GitHub Actions and Docker
    # builds. Re-lock with `make lock` if this fails.
    registries = set(_REGISTRY.findall((project_root / "uv.lock").read_text(encoding="utf-8")))

    assert registries == {PYPI_SIMPLE}


def test_pyproject_pins_pypi_as_default_index(project_root: Path) -> None:
    config = tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8"))
    tool = config["tool"]
    assert isinstance(tool, dict)

    assert {"name": "pypi", "url": PYPI_SIMPLE, "default": True} in tool["uv"]["index"]


@pytest.mark.parametrize("ignore_file", _IGNORE_FILES)
@pytest.mark.parametrize("path", _MUST_EXCLUDE)
def test_ignore_files_exclude_secrets_and_non_runtime_code(
    project_root: Path, ignore_file: str, path: str
) -> None:
    assert _ignore_spec(project_root / ignore_file).match_file(path), f"{ignore_file}: {path}"


@pytest.mark.parametrize("ignore_file", _IGNORE_FILES)
@pytest.mark.parametrize("path", _MUST_INCLUDE)
def test_ignore_files_keep_build_inputs(project_root: Path, ignore_file: str, path: str) -> None:
    assert not _ignore_spec(project_root / ignore_file).match_file(path), f"{ignore_file}: {path}"


def test_gcloudignore_mirrors_dockerignore(project_root: Path) -> None:
    assert _patterns(project_root / ".gcloudignore") == _patterns(project_root / ".dockerignore")


def test_gitignore_excludes_local_secrets_and_state(project_root: Path) -> None:
    spec = _ignore_spec(project_root / ".gitignore")

    for path in (".env", "terraform/terraform.tfvars", "deployment_metadata.json", "key.json"):
        assert spec.match_file(path), path
    assert not spec.match_file(".env.example")
    assert not spec.match_file("terraform/terraform.tfvars.example")


def test_toolchain_versions_are_consistent(project_root: Path) -> None:
    dockerfile = (project_root / "Dockerfile").read_text(encoding="utf-8")
    docker_uv = re.search(r"ghcr\.io/astral-sh/uv:([\d.]+)", dockerfile)
    docker_python = re.search(r"^FROM python:([\d.]+)", dockerfile, re.MULTILINE)
    assert docker_uv and docker_python

    for workflow in sorted((project_root / ".github" / "workflows").glob("*.yml")):
        data = yaml.safe_load(workflow.read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        for job in data["jobs"].values():
            for step in job.get("steps", []):
                if str(step.get("uses", "")).startswith("astral-sh/setup-uv@"):
                    options = step.get("with", {})
                    assert options.get("version") == docker_uv.group(1), workflow.name
                    assert options.get("python-version") == docker_python.group(1), workflow.name


def test_dockerfile_installs_frozen_dependencies_from_pypi(project_root: Path) -> None:
    dockerfile = (project_root / "Dockerfile").read_text(encoding="utf-8")

    assert f"uv sync --frozen --no-dev --default-index {PYPI_SIMPLE}" in dockerfile
    assert "app.fast_api_app:app" in dockerfile


def test_manifest_points_agents_cli_at_the_app_package(project_root: Path) -> None:
    manifest = yaml.safe_load((project_root / "agents-cli-manifest.yaml").read_text("utf-8"))
    assert isinstance(manifest, dict)

    assert manifest["agent_directory"] == "app"
    assert manifest["create_params"]["agent_guidance_filename"] == "AGENTS.md"
    assert manifest["create_params"]["deployment_target"] in {"agent_runtime", "cloud_run"}
