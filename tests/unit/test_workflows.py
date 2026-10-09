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

"""Static hygiene tests for every GitHub Actions workflow."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

import pytest
import yaml

_WORKFLOW_DIR: Final[Path] = Path(__file__).resolve().parents[2] / ".github" / "workflows"
WORKFLOWS: Final[list[Path]] = sorted(_WORKFLOW_DIR.glob("*.yml"))

EXPECTED_WORKFLOWS: Final[frozenset[str]] = frozenset(
    {
        "ci.yml",
        "deploy.yml",
        "eval.yml",
        "review-agent.yml",
        "tf-apply.yml",
        "tf-ci.yml",
    }
)
GCP_GATE: Final[str] = "vars.GCP_WIF_PROVIDER != ''"
FORK_GATE: Final[str] = "github.event.pull_request.head.repo.full_name == github.repository"
# Dependabot pull requests run with a read-only token and cannot mint OIDC tokens.
DEPENDABOT_GATE: Final[str] = "github.actor != 'dependabot[bot]'"

_USES_LINE: Final[re.Pattern[str]] = re.compile(r"^\s*(?:-\s+)?uses:\s*(\S+)(.*)$")
_PINNED_REF: Final[re.Pattern[str]] = re.compile(r"^[\w.-]+/[\w.-]+(?:/[\w./-]+)?@[0-9a-f]{40}$")
_VERSION_COMMENT: Final[re.Pattern[str]] = re.compile(r"#\s*v\d+(?:\.\d+)*")
_HARDCODED_IDENTIFIERS: Final[dict[str, re.Pattern[str]]] = {
    "project number": re.compile(r"\b\d{12}\b"),
    "service account email": re.compile(r"[\w.-]+@[\w-]+\.iam\.gserviceaccount\.com"),
    "workload identity pool": re.compile(r"workloadIdentityPools/[\w-]+"),
    "generated project ID": re.compile(r"\b[a-z][a-z0-9-]{4,28}-\d{6}\b"),
}
# Write is required: review.json is the only artifact that crosses into the publish step.
_CLAUDE_FILE_TOOLS: Final[frozenset[str]] = frozenset({"Read", "Grep", "Glob", "Write"})
_CLAUDE_READ_ONLY_COMMANDS: Final[frozenset[str]] = frozenset(
    {
        "Bash(gh pr diff:*)",
        "Bash(gh pr view:*)",
        "Bash(git diff:*)",
        "Bash(git log:*)",
        "Bash(git show:*)",
    }
)

type Mapping = dict[str, object]


def _as_mapping(value: object) -> Mapping:
    assert isinstance(value, dict), f"expected a mapping, got {type(value).__name__}"
    return {str(key): item for key, item in value.items()}


def _load(path: Path) -> Mapping:
    return _as_mapping(yaml.safe_load(path.read_text(encoding="utf-8")))


def _triggers(workflow: Mapping) -> Mapping:
    # PyYAML (YAML 1.1) parses the bare `on` key as the boolean True.
    raw = workflow.get("on", workflow.get("True"))
    if isinstance(raw, str):
        return {raw: None}
    if isinstance(raw, list):
        return {str(item): None for item in raw}
    return _as_mapping(raw)


def _jobs(workflow: Mapping) -> dict[str, Mapping]:
    return {name: _as_mapping(job) for name, job in _as_mapping(workflow["jobs"]).items()}


def _steps(job: Mapping) -> list[Mapping]:
    raw = job.get("steps", [])
    assert isinstance(raw, list)
    return [_as_mapping(step) for step in raw]


def _uses(step: Mapping) -> str:
    value = step.get("uses", "")
    return value if isinstance(value, str) else ""


def _job_if(job: Mapping) -> str:
    value = job.get("if", "")
    return " ".join(value.split()) if isinstance(value, str) else ""


def _needs_gcp(job: Mapping) -> bool:
    permissions = job.get("permissions")
    requests_oidc = isinstance(permissions, dict) and permissions.get("id-token") == "write"
    authenticates = any(
        _uses(step).startswith("google-github-actions/auth@") for step in _steps(job)
    )
    return requests_oidc or authenticates


def _workflow_ids() -> list[str]:
    return [path.name for path in WORKFLOWS]


def test_expected_workflows_exist() -> None:
    assert {path.name for path in WORKFLOWS} == EXPECTED_WORKFLOWS


@pytest.mark.parametrize("path", WORKFLOWS, ids=_workflow_ids())
def test_actions_are_pinned_to_commit_shas(path: Path) -> None:
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _USES_LINE.match(line)
        if not match or match.group(1).startswith("./"):
            continue
        reference, trailer = match.group(1), match.group(2)
        assert _PINNED_REF.match(reference), f"{path.name}: unpinned action {reference}"
        assert _VERSION_COMMENT.search(trailer), f"{path.name}: add a '# vX.Y.Z' to {reference}"


@pytest.mark.parametrize("path", WORKFLOWS, ids=_workflow_ids())
def test_top_level_permissions_are_least_privilege(path: Path) -> None:
    workflow = _load(path)

    assert "permissions" in workflow, f"{path.name}: declare top-level permissions"
    assert workflow["permissions"] != "write-all"
    for name, job in _jobs(workflow).items():
        assert job.get("permissions") != "write-all", f"{path.name}:{name} uses write-all"


@pytest.mark.parametrize("path", WORKFLOWS, ids=_workflow_ids())
def test_no_hardcoded_cloud_identifiers(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    for label, pattern in _HARDCODED_IDENTIFIERS.items():
        assert not pattern.search(text), f"{path.name}: hardcoded {label}; use vars.* instead"


@pytest.mark.parametrize("path", WORKFLOWS, ids=_workflow_ids())
def test_gcp_jobs_are_gated_on_bootstrap(path: Path) -> None:
    workflow = _load(path)
    runs_on_pull_requests = "pull_request" in _triggers(workflow)
    for name, job in _jobs(workflow).items():
        if not _needs_gcp(job):
            continue
        condition = _job_if(job)
        assert GCP_GATE in condition, f"{path.name}:{name} must be gated on {GCP_GATE}"
        if runs_on_pull_requests:
            assert FORK_GATE in condition, f"{path.name}:{name} must skip fork pull requests"
            assert DEPENDABOT_GATE in condition, f"{path.name}:{name} must skip Dependabot"


@pytest.mark.parametrize("path", WORKFLOWS, ids=_workflow_ids())
def test_checkouts_do_not_persist_credentials(path: Path) -> None:
    for name, job in _jobs(_load(path)).items():
        for step in _steps(job):
            if _uses(step).startswith("actions/checkout@"):
                options = _as_mapping(step.get("with", {}))
                assert options.get("persist-credentials") is False, f"{path.name}:{name}"


@pytest.mark.parametrize("path", WORKFLOWS, ids=_workflow_ids())
def test_run_steps_never_interpolate_expressions(path: Path) -> None:
    for name, job in _jobs(_load(path)).items():
        for step in _steps(job):
            script = step.get("run")
            if isinstance(script, str):
                assert "${{" not in script, (
                    f"{path.name}:{name}: pass values to run: through env, not ${{{{ }}}}"
                )


@pytest.mark.parametrize("path", WORKFLOWS, ids=_workflow_ids())
def test_jobs_have_timeouts(path: Path) -> None:
    for name, job in _jobs(_load(path)).items():
        assert isinstance(job.get("timeout-minutes"), int), f"{path.name}:{name}"


def test_static_checks_run_without_gcp() -> None:
    ci_jobs = _jobs(_load(_WORKFLOW_DIR / "ci.yml"))
    deploy_jobs = _jobs(_load(_WORKFLOW_DIR / "deploy.yml"))
    tf_jobs = _jobs(_load(_WORKFLOW_DIR / "tf-ci.yml"))

    assert all("vars." not in _job_if(job) for job in ci_jobs.values())
    assert "vars." not in _job_if(deploy_jobs["container-build"])
    static_tf_jobs = [job for job in tf_jobs.values() if not _needs_gcp(job)]
    assert static_tf_jobs, "tf-ci.yml needs an ungated fmt/validate job"
    assert all("vars." not in _job_if(job) for job in static_tf_jobs)


def test_deploy_workflow_delegates_to_deploy_script() -> None:
    steps = _steps(_jobs(_load(_WORKFLOW_DIR / "deploy.yml"))["deploy"])
    scripts = [step["run"] for step in steps if isinstance(step.get("run"), str)]

    assert "./scripts/deploy.sh --dry-run" in scripts
    assert "./scripts/deploy.sh" in scripts


def test_agent_version_flag_is_never_used(project_root: Path) -> None:
    # agents-cli 1.5.0 rejects --agent-version for Agent Runtime deployments.
    for path in [*WORKFLOWS, project_root / "scripts" / "deploy.sh"]:
        code = [
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        ]
        assert not [line for line in code if "--agent-version" in line], path.name


def test_review_agent_keeps_publisher_hardening() -> None:
    text = (_WORKFLOW_DIR / "review-agent.yml").read_text(encoding="utf-8")

    assert "RUNNER_TEMP" in text, "publisher must be staged outside the workspace"
    assert "sha256sum" in text, "staged publisher digest must be verified"
    assert "vars.REVIEW_MODEL" in text
    allowed_tools = re.findall(r"--allowedTools '([^']+)'", text)
    assert allowed_tools, "Claude must run with an explicit --allowedTools allowlist"
    for tool_list in allowed_tools:
        for tool in tool_list.split(","):
            assert tool in _CLAUDE_FILE_TOOLS or tool in _CLAUDE_READ_ONLY_COMMANDS, tool
