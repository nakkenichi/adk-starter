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

"""Behavioral tests for scripts/deploy.sh using stubbed uvx/gcloud/git (no cloud calls)."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pytest

_CALL_SEPARATOR: Final[str] = "::end-of-call::"
_STUBBED_SERVICE_URL: Final[str] = "https://demo-agent-abc123.a.run.app"
_BASE_ENV: Final[dict[str, str]] = {
    "GCP_PROJECT_ID": "demo-project",
    "GCP_REGION": "asia-northeast1",
    "SERVICE_NAME": "demo-agent",
}


@dataclass(frozen=True)
class DeployRun:
    """Captured result of one deploy.sh invocation."""

    returncode: int
    stdout: str
    stderr: str
    calls: list[list[str]]

    def calls_to(self, command: str) -> list[list[str]]:
        """Returns the argument lists of every call to the given stubbed command."""
        return [call[1:] for call in self.calls if call[0] == command]


def _flag_value(args: list[str], flag: str) -> str:
    return args[args.index(flag) + 1]


@pytest.fixture
def run_deploy(tmp_path: Path, project_root: Path) -> object:
    """Returns a callable running deploy.sh with stub binaries first on PATH."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    record = f'{{ printf "%s\\n" "$(basename "$0")" "$@"; echo "{_CALL_SEPARATOR}"; }} >> "{log}"'
    stubs = {
        "uvx": record,
        "gcloud": f'{record}\necho "{_STUBBED_SERVICE_URL}"',
        "git": 'echo "abc1234"',
    }
    for name, body in stubs.items():
        stub = bin_dir / name
        stub.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
        stub.chmod(0o755)

    def _run(*args: str, **env: str) -> DeployRun:
        full_env = {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "HOME": str(tmp_path)}
        full_env.update(env)
        completed = subprocess.run(
            ["bash", str(project_root / "scripts" / "deploy.sh"), *args],
            env=full_env,
            capture_output=True,
            text=True,
            check=False,
        )
        raw = log.read_text(encoding="utf-8") if log.exists() else ""
        calls = [
            [line for line in chunk.strip("\n").split("\n") if line]
            for chunk in raw.split(f"{_CALL_SEPARATOR}\n")
            if chunk.strip()
        ]
        return DeployRun(completed.returncode, completed.stdout, completed.stderr, calls)

    return _run


type RunDeploy = object


def _invoke(run_deploy: RunDeploy, *args: str, **env: str) -> DeployRun:
    assert callable(run_deploy)
    result = run_deploy(*args, **env)
    assert isinstance(result, DeployRun)
    return result


def test_agent_runtime_deploy_uses_source_build_flags(run_deploy: RunDeploy) -> None:
    run = _invoke(run_deploy, **_BASE_ENV, GCP_RUNTIME_SA="runtime@demo.example")

    assert run.returncode == 0, run.stderr
    [deploy] = run.calls_to("uvx")
    assert deploy[:2] == ["google-agents-cli==1.5.0", "deploy"]
    assert _flag_value(deploy, "--deployment-target") == "agent_runtime"
    assert _flag_value(deploy, "--project") == "demo-project"
    assert _flag_value(deploy, "--region") == "asia-northeast1"
    assert _flag_value(deploy, "--service-name") == "demo-agent"
    assert _flag_value(deploy, "--service-account") == "runtime@demo.example"
    assert "--no-confirm-project" in deploy
    assert "--image" not in deploy
    assert "--agent-version" not in deploy
    assert "--dry-run" not in deploy
    env_vars = _flag_value(deploy, "--update-env-vars").split(",")
    assert "GOOGLE_CLOUD_LOCATION=global" in env_vars
    assert "AGENT_VERSION=abc1234" in env_vars
    assert not any(item.startswith("GOOGLE_CLOUD_PROJECT=") for item in env_vars)


def test_sizing_and_buckets_are_configurable(run_deploy: RunDeploy) -> None:
    run = _invoke(
        run_deploy,
        **_BASE_ENV,
        DEPLOY_MEMORY="8Gi",
        DEPLOY_MAX_INSTANCES="3",
        LOGS_BUCKET_NAME="demo-logs",
        EXPORTS_BUCKET_NAME="demo-exports",
        AGENT_VERSION="1.2.3",
    )

    [deploy] = run.calls_to("uvx")
    assert _flag_value(deploy, "--memory") == "8Gi"
    assert _flag_value(deploy, "--max-instances") == "3"
    env_vars = _flag_value(deploy, "--update-env-vars").split(",")
    assert {"LOGS_BUCKET_NAME=demo-logs", "EXPORTS_BUCKET_NAME=demo-exports"} <= set(env_vars)
    assert "AGENT_VERSION=1.2.3" in env_vars


@pytest.mark.parametrize(
    ("image_uri", "expect_image_flag"),
    [("asia-northeast1-docker.pkg.dev/demo-project/demo-repo/demo-agent:abc", True), ("", False)],
)
def test_cloud_run_deploy(run_deploy: RunDeploy, image_uri: str, expect_image_flag: bool) -> None:
    run = _invoke(run_deploy, **_BASE_ENV, DEPLOYMENT_TARGET="cloud_run", IMAGE_URI=image_uri)

    assert run.returncode == 0, run.stderr
    [deploy] = run.calls_to("uvx")
    assert _flag_value(deploy, "--deployment-target") == "cloud_run"
    assert ("--image" in deploy) is expect_image_flag
    if expect_image_flag:
        assert _flag_value(deploy, "--image") == image_uri
    env_vars = _flag_value(deploy, "--update-env-vars").split(",")
    assert "GOOGLE_CLOUD_PROJECT=demo-project" in env_vars


@pytest.mark.parametrize("target", ["agent_runtime", "cloud_run"])
def test_dry_run_never_publishes(run_deploy: RunDeploy, target: str) -> None:
    run = _invoke(
        run_deploy,
        "--dry-run",
        **_BASE_ENV,
        DEPLOYMENT_TARGET=target,
        GEMINI_ENTERPRISE_APP_ID="projects/1/locations/global/collections/c/engines/e",
    )

    assert run.returncode == 0, run.stderr
    [deploy] = run.calls_to("uvx")
    assert "--dry-run" in deploy
    assert run.calls_to("gcloud") == []
    assert "would register" in run.stdout


def test_agent_runtime_publishes_adk_registration(run_deploy: RunDeploy) -> None:
    run = _invoke(run_deploy, **_BASE_ENV, GEMINI_ENTERPRISE_APP_ID="projects/1/engines/e")

    assert run.returncode == 0, run.stderr
    deploy, publish = run.calls_to("uvx")
    assert deploy[1] == "deploy"
    assert publish[1:3] == ["publish", "gemini-enterprise"]
    assert _flag_value(publish, "--registration-type") == "adk"
    assert _flag_value(publish, "--gemini-enterprise-app-id") == "projects/1/engines/e"
    assert _flag_value(publish, "--display-name") == "demo-agent"


def test_cloud_run_publishes_a2a_registration(run_deploy: RunDeploy) -> None:
    run = _invoke(
        run_deploy,
        **_BASE_ENV,
        DEPLOYMENT_TARGET="cloud_run",
        GEMINI_ENTERPRISE_APP_ID="projects/1/engines/e",
        AGENT_DISPLAY_NAME="Demo Agent",
    )

    assert run.returncode == 0, run.stderr
    [describe] = run.calls_to("gcloud")
    assert describe[:4] == ["run", "services", "describe", "demo-agent"]
    _, publish = run.calls_to("uvx")
    assert _flag_value(publish, "--registration-type") == "a2a"
    assert _flag_value(publish, "--agent-card-url") == (
        f"{_STUBBED_SERVICE_URL}/a2a/app/.well-known/agent-card.json"
    )
    assert _flag_value(publish, "--display-name") == "Demo Agent"


@pytest.mark.parametrize("missing", sorted(_BASE_ENV))
def test_required_environment_is_enforced(run_deploy: RunDeploy, missing: str) -> None:
    env = {key: value for key, value in _BASE_ENV.items() if key != missing}

    run = _invoke(run_deploy, **env)

    assert run.returncode != 0
    assert missing in run.stderr
    assert run.calls == []


@pytest.mark.parametrize(
    ("args", "env_overrides", "expected_code"),
    [
        ((), {"DEPLOYMENT_TARGET": "gke"}, 2),
        (("--bogus",), {}, 2),
        (("--help",), {}, 0),
    ],
)
def test_argument_and_target_validation(
    run_deploy: RunDeploy,
    args: tuple[str, ...],
    env_overrides: dict[str, str],
    expected_code: int,
) -> None:
    run = _invoke(run_deploy, *args, **{**_BASE_ENV, **env_overrides})

    assert run.returncode == expected_code
    assert run.calls == []
    if expected_code == 0:
        assert "Usage: scripts/deploy.sh" in run.stdout
