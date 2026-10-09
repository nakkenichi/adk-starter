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

"""Static invariants of the Terraform stack, its CI workflows and the bootstrap script.

Everything is checked by parsing the HCL, YAML and shell sources as text: no terraform binary,
no network and no Google Cloud credentials are needed. The tests pin what keeps the template
safe to reuse across projects: CI/CD resources are count-gated, IAM is non-authoritative, the
state bucket is hardened, every name derives from ``resource_prefix``, no customer identifiers
leak in, and Terraform, the workflows and ``scripts/bootstrap_gcp.sh`` agree on their
contracts (``TF_VAR_*`` inputs, repository variables and the state bucket name).
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

TF_CI_WORKFLOW = ".github/workflows/tf-ci.yml"
TF_APPLY_WORKFLOW = ".github/workflows/tf-apply.yml"
BOOTSTRAP_SCRIPT = "scripts/bootstrap_gcp.sh"
TFVARS_EXAMPLE = "terraform/terraform.tfvars.example"

EXPECTED_TF_FILES: frozenset[str] = frozenset(
    {
        "artifact_registry.tf",
        "iam.tf",
        "migrations.tf",
        "outputs.tf",
        "service_accounts.tf",
        "services.tf",
        "storage.tf",
        "variables.tf",
        "versions.tf",
        "wif.tf",
    }
)

# Variables that must never get a default (no project-specific defaults).
REQUIRED_VARIABLES: frozenset[str] = frozenset({"project_id", "project_number", "resource_prefix"})

# Repository variables published by scripts/bootstrap_gcp.sh from this Terraform output.
GITHUB_ACTIONS_VARIABLE_KEYS: frozenset[str] = frozenset(
    {
        "GCP_PROJECT_ID",
        "GCP_PROJECT_NUMBER",
        "GCP_REGION",
        "RESOURCE_PREFIX",
        "DEPLOYMENT_TARGET",
        "GCP_WIF_PROVIDER",
        "GCP_CICD_SA",
        "GCP_RUNTIME_SA",
        "ARTIFACT_REPO",
        "LOGS_BUCKET_NAME",
        "EXPORTS_BUCKET_NAME",
    }
)

# Repository variables the Terraform workflows may read that maintainers set by hand.
OPTIONAL_WORKFLOW_VARIABLES: frozenset[str] = frozenset({"GEMINI_ENTERPRISE_APP_ID"})

EXPECTED_WORKFLOW_TF_ENV: dict[str, str] = {
    "TF_VAR_project_id": "${{ vars.GCP_PROJECT_ID }}",
    "TF_VAR_project_number": "${{ vars.GCP_PROJECT_NUMBER }}",
    "TF_VAR_region": "${{ vars.GCP_REGION }}",
    "TF_VAR_resource_prefix": "${{ vars.RESOURCE_PREFIX }}",
    "TF_VAR_github_org": "${{ github.repository_owner }}",
    "TF_VAR_github_repo": "${{ github.event.repository.name }}",
    "TF_VAR_deployment_target": "${{ vars.DEPLOYMENT_TARGET || 'agent_runtime' }}",
    "TF_VAR_enable_gemini_enterprise": "${{ vars.GEMINI_ENTERPRISE_APP_ID != '' }}",
    "TF_VAR_enable_cicd": "true",
}

# Count-gated CI/CD identity: anything referencing these must be gated on var.enable_cicd too.
CICD_ROOT_RESOURCES: frozenset[str] = frozenset(
    {
        "google_service_account.cicd_runner_sa",
        "google_iam_workload_identity_pool.github_pool",
        "google_iam_workload_identity_pool_provider.github_provider",
    }
)
MIN_EXPECTED_CICD_RESOURCES: frozenset[str] = CICD_ROOT_RESOURCES | {
    "google_storage_bucket_iam_member.cicd_state_admin",
    "google_artifact_registry_repository_iam_member.cicd_ar_writer",
    "google_project_iam_member.cicd_sa_roles",
    "google_service_account_iam_member.cicd_actas_app_sa",
    "google_service_account_iam_member.cicd_wif_user",
    "google_service_account_iam_member.cicd_sa_self_token_creator",
}

# Generic fingerprints of a real environment. Concrete identifiers are deliberately not listed:
# every repository created from this template would otherwise ship them.
GENERATED_PROJECT_ID = re.compile(r"\b[a-z][a-z0-9-]{4,28}-\d{6}\b")
LITERAL_PROJECT_ARGUMENT = re.compile(r'^[ \t]*project(?:_id)?[ \t]*=[ \t]*"', re.MULTILINE)
LITERAL_SA_EMAIL = re.compile(r"[a-z0-9-]+@[a-z0-9-]+\.iam\.gserviceaccount\.com")
PROJECT_NUMBER_LIKE = re.compile(r"(?<![\w.-])\d{10,13}(?![\w.-])")
PLACEHOLDER_PROJECT_NUMBER = "123456789012"

MAX_PREFIX = "p" * 16
MAX_PROJECT_ID = "a" * 30

_BLOCK_HEADER = re.compile(
    r'^(?P<kind>[a-z_]+)(?P<labels>(?:[ \t]+"[^"\n]*")*)[ \t]*\{[ \t]*$', re.MULTILINE
)
_FULL_LINE_COMMENT = re.compile(r"^[ \t]*(?:#|//).*$", re.MULTILINE)


@dataclass(frozen=True)
class HclBlock:
    """A top-level block of a Terraform file.

    Attributes:
        kind: Block type, e.g. ``resource``, ``variable`` or ``output``.
        labels: Quoted block labels, e.g. ``("google_storage_bucket", "tf_state")``.
        body: Raw text between the block's outer braces (full-line comments removed).
        source: File the block was read from.
    """

    kind: str
    labels: tuple[str, ...]
    body: str
    source: Path

    @property
    def address(self) -> str:
        """Returns the Terraform address (``type.name``) of a labelled block."""
        return ".".join(self.labels)


def _normalize(text: str) -> str:
    """Collapses every whitespace run to a single space."""
    return re.sub(r"\s+", " ", text).strip()


def _block_body(text: str, *, open_index: int) -> str:
    """Returns the text between the brace at ``open_index`` and its matching brace.

    Args:
        text: HCL source with full-line comments removed.
        open_index: Index of the opening ``{``.

    Returns:
        The block body without the outer braces.

    Raises:
        AssertionError: If the braces are unbalanced.
    """
    depth = 0
    for index in range(open_index, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[open_index + 1 : index]
    raise AssertionError(f"Unbalanced braces after offset {open_index}")


def _parse_blocks(path: Path) -> list[HclBlock]:
    """Parses the top-level blocks of one ``.tf`` file.

    Top-level blocks start at column 0 (``terraform fmt`` guarantees it). Braces inside
    trailing comments are not supported; full-line comments are stripped first.

    Args:
        path: Terraform file to parse.

    Returns:
        The blocks in file order.
    """
    text = _FULL_LINE_COMMENT.sub("", path.read_text(encoding="utf-8"))
    blocks: list[HclBlock] = []
    for match in _BLOCK_HEADER.finditer(text):
        labels = tuple(re.findall(r'"([^"\n]*)"', match.group("labels")))
        body = _block_body(text, open_index=match.start() + match.group(0).rindex("{"))
        blocks.append(HclBlock(kind=match.group("kind"), labels=labels, body=body, source=path))
    return blocks


def _find_block(blocks: tuple[HclBlock, ...], *, kind: str, address: str) -> HclBlock:
    """Returns the unique block of ``kind`` with ``address``.

    Raises:
        AssertionError: If the block is missing or defined more than once.
    """
    found = [block for block in blocks if block.kind == kind and block.address == address]
    assert len(found) == 1, f"Expected exactly one {kind} {address!r}, found {len(found)}"
    return found[0]


def _attribute(block: HclBlock, *, name: str) -> str:
    """Returns the raw expression assigned to a top-level attribute of ``block``.

    Raises:
        AssertionError: If the attribute is missing.
    """
    match = re.search(rf"^[ \t]*{re.escape(name)}[ \t]*=[ \t]*(?P<value>.+)$", block.body, re.M)
    assert match, f"{block.address or block.kind}: attribute {name!r} not found"
    return match.group("value").strip()


def _variables(blocks: tuple[HclBlock, ...]) -> dict[str, HclBlock]:
    """Maps every declared input variable name to its block."""
    return {block.labels[0]: block for block in blocks if block.kind == "variable"}


def _load_workflow_jobs(path: Path) -> dict[str, dict[str, object]]:
    """Loads the ``jobs`` mapping of a GitHub Actions workflow.

    Args:
        path: Workflow file.

    Returns:
        Job id mapped to the job definition.
    """
    loaded: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict), f"{path} is not a YAML mapping"
    jobs = loaded.get("jobs")
    assert isinstance(jobs, dict), f"{path} has no jobs mapping"
    result: dict[str, dict[str, object]] = {}
    for job_id, job in jobs.items():
        assert isinstance(job, dict), f"{path}: job {job_id!r} is not a mapping"
        result[str(job_id)] = {str(key): value for key, value in job.items()}
    return result


def _job_env(job: dict[str, object]) -> dict[str, str]:
    """Returns a job's ``env`` mapping with string values."""
    env = job.get("env", {})
    assert isinstance(env, dict), "job env must be a mapping"
    return {str(key): str(value) for key, value in env.items()}


def _job_run_scripts(job: dict[str, object]) -> list[str]:
    """Returns the ``run`` scripts of every step of a job."""
    steps = job.get("steps", [])
    assert isinstance(steps, list), "job steps must be a list"
    return [str(step["run"]) for step in steps if isinstance(step, dict) and "run" in step]


def _scanned_files(project_root: Path) -> list[Path]:
    """Returns the files that must stay free of customer identifiers."""
    terraform_files = [
        path
        for path in sorted((project_root / "terraform").rglob("*"))
        if path.is_file()
        and ".terraform" not in path.relative_to(project_root).parts
        and path.name != ".terraform.lock.hcl"
    ]
    extra = [project_root / rel for rel in (BOOTSTRAP_SCRIPT, TF_CI_WORKFLOW, TF_APPLY_WORKFLOW)]
    return terraform_files + extra


@pytest.fixture(scope="module")
def tf_blocks(project_root: Path) -> tuple[HclBlock, ...]:
    """Every top-level block of ``terraform/*.tf``."""
    paths = sorted((project_root / "terraform").glob("*.tf"))
    return tuple(block for path in paths for block in _parse_blocks(path))


def test_terraform_layout_is_complete(project_root: Path) -> None:
    """The stack keeps its one-concern-per-file layout plus the example and CI helper."""
    tf_names = {path.name for path in (project_root / "terraform").glob("*.tf")}
    assert EXPECTED_TF_FILES <= tf_names, f"Missing: {sorted(EXPECTED_TF_FILES - tf_names)}"
    assert (project_root / TFVARS_EXAMPLE).is_file()
    assert (project_root / "terraform" / "scripts" / "ci" / "post_pr_comment.js").is_file()


@pytest.mark.parametrize(
    ("rel_path", "required_pattern", "prohibited_pattern"),
    [
        pytest.param(
            "terraform/versions.tf",
            r'required_version\s*=\s*">= 1\.5\.0"',
            r'backend\s+"gcs"\s*\{[^}]*\bbucket\s*=',
            id="tf_version_and_bucket_supplied_at_init",
        ),
        pytest.param(
            "terraform/variables.tf",
            r'variable\s+"enable_cicd"\s*\{[^}]*default\s*=\s*false',
            r'variable\s+"enable_cicd"\s*\{[^}]*default\s*=\s*true',
            id="tf_enable_cicd_defaults_to_false",
        ),
        pytest.param(
            "terraform/outputs.tf",
            r"one\(google_service_account\.cicd_runner_sa\[\*\]\.email\)",
            r"try\(",
            id="tf_outputs_use_one_for_gated_resources",
        ),
        pytest.param(
            "terraform/outputs.tf",
            r"one\(google_iam_workload_identity_pool_provider\.github_provider\[\*\]\.name\)",
            r"",
            id="tf_outputs_wif_provider_uses_one",
        ),
        pytest.param(
            "terraform/migrations.tf",
            r"moved\s*\{",
            r"",
            id="tf_migrations_documents_moved_convention",
        ),
        pytest.param(
            "terraform/services.tf",
            r"concat\(local\.essential_services,\s*var\.additional_services\)",
            r"disable_on_destroy\s*=\s*true",
            id="tf_services_extensible_and_never_disabled",
        ),
    ],
)
def test_terraform_file_invariants(
    rel_path: str,
    required_pattern: str,
    prohibited_pattern: str,
    project_root: Path,
) -> None:
    """Each Terraform file keeps its required pattern and never regains a prohibited one.

    Args:
        rel_path: Terraform file relative to the repository root.
        required_pattern: Regex that must match within the file.
        prohibited_pattern: Regex that must not match within the file (skipped if empty).
        project_root: Repository root path provided by the session fixture.
    """
    content = (project_root / rel_path).read_text(encoding="utf-8")
    assert re.search(required_pattern, content), f"{rel_path}: missing /{required_pattern}/"
    if prohibited_pattern:
        assert not re.search(prohibited_pattern, content), (
            f"{rel_path}: prohibited /{prohibited_pattern}/ matched"
        )


def test_only_project_identity_variables_lack_defaults(tf_blocks: tuple[HclBlock, ...]) -> None:
    """project_id, project_number and resource_prefix must always be passed explicitly."""
    without_default = {
        name
        for name, block in _variables(tf_blocks).items()
        if not re.search(r"^[ \t]*default[ \t]*=", block.body, re.M)
    }
    assert without_default == REQUIRED_VARIABLES


def test_cicd_resources_are_gated_on_enable_cicd(tf_blocks: tuple[HclBlock, ...]) -> None:
    """Every CI/CD resource, and anything referencing one, is gated on var.enable_cicd.

    Ungated references to ``[0]`` of a count-gated resource would break customer applies
    with ``enable_cicd = false``.
    """
    gate = re.compile(r"^[ \t]*(?:count|for_each)[ \t]*=[ \t]*var\.enable_cicd[ \t]*\?", re.M)
    cicd_resources = {
        block.address: block
        for block in tf_blocks
        if block.kind == "resource"
        and (
            block.address in CICD_ROOT_RESOURCES
            or any(root in block.body for root in CICD_ROOT_RESOURCES)
        )
    }
    missing = MIN_EXPECTED_CICD_RESOURCES - cicd_resources.keys()
    assert not missing, f"Expected CI/CD resources not found: {sorted(missing)}"
    ungated = sorted(
        address for address, block in cicd_resources.items() if not gate.search(block.body)
    )
    assert not ungated, f"CI/CD resources not gated on var.enable_cicd: {ungated}"


def test_iam_is_non_authoritative(tf_blocks: tuple[HclBlock, ...]) -> None:
    """Only additive *_iam_member resources are used, never *_iam_binding or *_iam_policy."""
    iam_types = {
        block.labels[0]
        for block in tf_blocks
        if block.kind == "resource" and "_iam_" in block.labels[0]
    }
    authoritative = sorted(t for t in iam_types if re.search(r"_iam_(?:binding|policy)$", t))
    assert not authoritative, f"Authoritative IAM resources are forbidden: {authoritative}"
    assert any(t.endswith("_iam_member") for t in iam_types)


def test_every_bucket_enforces_uniform_access_and_public_access_prevention(
    tf_blocks: tuple[HclBlock, ...],
) -> None:
    """All buckets use uniform bucket-level access and enforced public access prevention."""
    buckets = [
        b for b in tf_blocks if b.kind == "resource" and b.labels[0] == "google_storage_bucket"
    ]
    assert len(buckets) >= 3
    for bucket in buckets:
        assert _attribute(bucket, name="uniform_bucket_level_access") == "true", bucket.address
        assert _attribute(bucket, name="public_access_prevention") == '"enforced"', bucket.address


@pytest.mark.parametrize(
    ("pattern", "reason"),
    [
        pytest.param(r"versioning\s*\{\s*enabled\s*=\s*true\s*\}", "versioning", id="versioning"),
        pytest.param(
            r"lifecycle\s*\{[^}]*prevent_destroy\s*=\s*true", "prevent_destroy", id="no_destroy"
        ),
        pytest.param(r"uniform_bucket_level_access\s*=\s*true", "UBLA", id="ubla"),
        pytest.param(r'public_access_prevention\s*=\s*"enforced"', "PAP", id="pap"),
    ],
)
def test_state_bucket_hardening(pattern: str, reason: str, tf_blocks: tuple[HclBlock, ...]) -> None:
    """The Terraform state bucket is versioned, protected from destroy and never public.

    Args:
        pattern: Regex that must match within the tf_state bucket block.
        reason: Human-readable name of the hardening property.
        tf_blocks: Parsed Terraform blocks.
    """
    state_bucket = _find_block(tf_blocks, kind="resource", address="google_storage_bucket.tf_state")
    assert re.search(pattern, state_bucket.body), f"tf_state bucket is missing {reason}"


def test_exports_bucket_expires_objects_after_seven_days(tf_blocks: tuple[HclBlock, ...]) -> None:
    """User-facing exports are short-lived: objects are deleted after 7 days."""
    exports = _find_block(tf_blocks, kind="resource", address="google_storage_bucket.exports")
    body = _normalize(exports.body)
    assert 'action { type = "Delete" }' in body
    assert "condition { age = 7 }" in body


@pytest.mark.parametrize(
    ("address", "attribute", "expected", "id_pattern"),
    [
        pytest.param(
            "google_service_account.cicd_runner_sa",
            "account_id",
            '"${var.resource_prefix}-cicd-sa"',
            r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$",
            id="cicd_sa",
        ),
        pytest.param(
            "google_service_account.app_sa",
            "account_id",
            '"${var.resource_prefix}-runtime-sa"',
            r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$",
            id="runtime_sa",
        ),
        pytest.param(
            "google_iam_workload_identity_pool.github_pool",
            "workload_identity_pool_id",
            '"${var.resource_prefix}-github-pool"',
            r"^[a-z0-9-]{4,32}$",
            id="wif_pool",
        ),
        pytest.param(
            "google_iam_workload_identity_pool_provider.github_provider",
            "workload_identity_pool_provider_id",
            '"${var.resource_prefix}-github-provider"',
            r"^[a-z0-9-]{4,32}$",
            id="wif_provider",
        ),
        pytest.param(
            "google_artifact_registry_repository.repo",
            "repository_id",
            '"${var.resource_prefix}-repo"',
            r"^[a-z][a-z0-9-]{0,61}[a-z0-9]$",
            id="artifact_registry",
        ),
        pytest.param(
            "google_storage_bucket.tf_state",
            "name",
            '"${local.project_id}-${var.resource_prefix}-tfstate"',
            r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$",
            id="state_bucket",
        ),
        pytest.param(
            "google_storage_bucket.logs",
            "name",
            '"${local.project_id}-${var.resource_prefix}-logs"',
            r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$",
            id="logs_bucket",
        ),
        pytest.param(
            "google_storage_bucket.exports",
            "name",
            '"${local.project_id}-${var.resource_prefix}-exports"',
            r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$",
            id="exports_bucket",
        ),
    ],
)
def test_named_resources_derive_from_resource_prefix(
    address: str,
    attribute: str,
    expected: str,
    id_pattern: str,
    tf_blocks: tuple[HclBlock, ...],
) -> None:
    """Every named resource derives from resource_prefix and fits GCP limits at 16 chars.

    Args:
        address: Terraform address of the named resource.
        attribute: Attribute holding the resource ID.
        expected: Exact HCL expression the attribute must have.
        id_pattern: GCP ID format the rendered name must satisfy.
        tf_blocks: Parsed Terraform blocks.
    """
    block = _find_block(tf_blocks, kind="resource", address=address)
    value = _attribute(block, name=attribute)
    assert value == expected

    rendered = (
        value.strip('"')
        .replace("${var.resource_prefix}", MAX_PREFIX)
        .replace("${local.project_id}", MAX_PROJECT_ID)
    )
    assert re.fullmatch(id_pattern, rendered), f"{address}: {rendered!r} breaks GCP ID limits"


def _resource_prefix_regex(blocks: tuple[HclBlock, ...]) -> str:
    """Extracts the resource_prefix validation regex from variables.tf."""
    body = _variables(blocks)["resource_prefix"].body
    match = re.search(r'regex\("(?P<regex>[^"]+)",\s*var\.resource_prefix\)', body)
    assert match, "resource_prefix must be validated with regex()"
    return match.group("regex")


@pytest.mark.parametrize(
    ("prefix", "valid"),
    [
        pytest.param("eat", True, id="min_length"),
        pytest.param("my-agent", True, id="hyphenated"),
        pytest.param(MAX_PREFIX, True, id="max_length_16"),
        pytest.param("ab", False, id="too_short"),
        pytest.param("p" * 17, False, id="too_long_17"),
        pytest.param("1agent", False, id="starts_with_digit"),
        pytest.param("agent-", False, id="ends_with_hyphen"),
        pytest.param("Agent", False, id="uppercase"),
        pytest.param("my_agent", False, id="underscore"),
    ],
)
def test_resource_prefix_validation(
    prefix: str, valid: bool, tf_blocks: tuple[HclBlock, ...]
) -> None:
    """The resource_prefix validation accepts exactly 3-16 char DNS-style prefixes.

    Args:
        prefix: Candidate prefix.
        valid: Whether Terraform must accept it.
        tf_blocks: Parsed Terraform blocks.
    """
    assert bool(re.search(_resource_prefix_regex(tf_blocks), prefix)) is valid


def test_bootstrap_validates_prefix_with_the_terraform_regex(
    project_root: Path, tf_blocks: tuple[HclBlock, ...]
) -> None:
    """scripts/bootstrap_gcp.sh rejects the same prefixes as Terraform, before any API call."""
    script = (project_root / BOOTSTRAP_SCRIPT).read_text(encoding="utf-8")
    assert f"PREFIX_REGEX='{_resource_prefix_regex(tf_blocks)}'" in script


def test_deployment_target_is_validated(tf_blocks: tuple[HclBlock, ...]) -> None:
    """deployment_target defaults to agent_runtime and only accepts the two supported targets."""
    block = _variables(tf_blocks)["deployment_target"]
    assert _attribute(block, name="default") == '"agent_runtime"'
    assert 'contains(["agent_runtime", "cloud_run"], var.deployment_target)' in block.body


def test_wif_trust_is_scoped_to_owner_and_repository(tf_blocks: tuple[HclBlock, ...]) -> None:
    """WIF accepts the configured owner only, and only one repository may impersonate the SA."""
    provider = _find_block(
        tf_blocks,
        kind="resource",
        address="google_iam_workload_identity_pool_provider.github_provider",
    )
    assert (
        _attribute(provider, name="attribute_condition")
        == "\"assertion.repository_owner == '${local.github_org}'\""
    )
    assert re.search(
        r'local\.github_org\s*!=\s*""\s*&&\s*local\.github_repo\s*!=\s*""', provider.body
    )

    wif_user = _find_block(
        tf_blocks, kind="resource", address="google_service_account_iam_member.cicd_wif_user"
    )
    assert _attribute(wif_user, name="member").endswith(
        '/attribute.repository/${local.github_org}/${local.github_repo}"'
    )


def test_gemini_enterprise_invoker_grant_is_conditional(tf_blocks: tuple[HclBlock, ...]) -> None:
    """roles/run.invoker goes only to the Discovery Engine agent, only for GE on Cloud Run."""
    grants = [b for b in tf_blocks if b.kind == "resource" and "roles/run.invoker" in b.body]
    assert grants, "Expected the Gemini Enterprise run.invoker grant"
    for grant in grants:
        assert (
            _normalize(_attribute(grant, name="count"))
            == 'var.enable_gemini_enterprise && var.deployment_target == "cloud_run" ? 1 : 0'
        ), grant.address
        assert "@gcp-sa-discoveryengine.iam.gserviceaccount.com" in _attribute(grant, name="member")


def test_github_actions_variables_output_keys(tf_blocks: tuple[HclBlock, ...]) -> None:
    """The output bootstrap publishes has exactly the keys the workflows rely on."""
    output = _find_block(tf_blocks, kind="output", address="github_actions_variables")
    match = re.search(
        r"value\s*=\s*var\.enable_cicd\s*\?\s*tomap\(\{(?P<entries>.*?)\}\)\s*:\s*tomap\(\{\s*\}\)",
        output.body,
        re.S,
    )
    assert match, "github_actions_variables must be `var.enable_cicd ? tomap({...}) : tomap({})`"
    keys = re.findall(r"^[ \t]*([A-Z][A-Z0-9_]*)[ \t]*=", match.group("entries"), re.M)
    assert len(keys) == len(set(keys)), f"Duplicate keys: {keys}"
    assert set(keys) == GITHUB_ACTIONS_VARIABLE_KEYS


@pytest.mark.parametrize(
    ("workflow", "job_id"),
    [
        pytest.param(TF_CI_WORKFLOW, "terraform-plan", id="tf_ci_plan"),
        pytest.param(TF_APPLY_WORKFLOW, "terraform-apply", id="tf_apply"),
    ],
)
def test_terraform_workflows_pass_inputs_via_tf_var_env(
    workflow: str, job_id: str, project_root: Path
) -> None:
    """GCP jobs get their inputs from TF_VAR_* env (repository variables), never -var literals.

    Args:
        workflow: Workflow path relative to the repository root.
        job_id: Job that runs terraform against GCP.
        project_root: Repository root path provided by the session fixture.
    """
    job = _load_workflow_jobs(project_root / workflow)[job_id]
    tf_env = {key: value for key, value in _job_env(job).items() if key.startswith("TF_VAR_")}
    assert tf_env == EXPECTED_WORKFLOW_TF_ENV
    for script in _job_run_scripts(job):
        assert not re.search(r"(?:^|\s)-var(?:-file)?[= ]", script), f"{workflow}: {script!r}"


@pytest.mark.parametrize(
    ("workflow", "job_id", "required_conditions"),
    [
        pytest.param(TF_CI_WORKFLOW, "terraform-static", (), id="tf_ci_static_always_runs"),
        pytest.param(
            TF_CI_WORKFLOW,
            "terraform-plan",
            (
                "vars.GCP_WIF_PROVIDER != ''",
                "github.event.pull_request.head.repo.full_name == github.repository",
            ),
            id="tf_ci_plan_gated",
        ),
        pytest.param(
            TF_APPLY_WORKFLOW,
            "terraform-apply",
            ("vars.GCP_WIF_PROVIDER != ''", "github.ref == 'refs/heads/main'"),
            id="tf_apply_gated",
        ),
    ],
)
def test_terraform_workflow_gates(
    workflow: str,
    job_id: str,
    required_conditions: tuple[str, ...],
    project_root: Path,
) -> None:
    """Static checks always run; GCP jobs skip cleanly until bootstrap sets the variables.

    Args:
        workflow: Workflow path relative to the repository root.
        job_id: Job to inspect.
        required_conditions: Conditions its ``if`` must contain (empty: no ``if`` at all).
        project_root: Repository root path provided by the session fixture.
    """
    job = _load_workflow_jobs(project_root / workflow)[job_id]
    condition = _normalize(str(job.get("if", "")))
    if not required_conditions:
        assert condition == "", f"{workflow}:{job_id} must not be gated"
    for required in required_conditions:
        assert required in condition, f"{workflow}:{job_id} must be gated on {required}"


@pytest.mark.parametrize(
    "workflow",
    [
        pytest.param(TF_CI_WORKFLOW, id="tf_ci"),
        pytest.param(TF_APPLY_WORKFLOW, id="tf_apply"),
    ],
)
def test_workflow_variables_are_published_by_bootstrap(workflow: str, project_root: Path) -> None:
    """Every repository variable a Terraform workflow reads is published or documented.

    Args:
        workflow: Workflow path relative to the repository root.
        project_root: Repository root path provided by the session fixture.
    """
    content = (project_root / workflow).read_text(encoding="utf-8")
    used = set(re.findall(r"\bvars\.([A-Z][A-Z0-9_]*)", content))
    unknown = used - GITHUB_ACTIONS_VARIABLE_KEYS - OPTIONAL_WORKFLOW_VARIABLES
    assert not unknown, f"{workflow} reads unpublished repository variables: {sorted(unknown)}"


@pytest.mark.parametrize(
    ("rel_path", "tf_var_pattern"),
    [
        pytest.param(TF_CI_WORKFLOW, r"^\s*TF_VAR_(\w+):", id="tf_ci"),
        pytest.param(TF_APPLY_WORKFLOW, r"^\s*TF_VAR_(\w+):", id="tf_apply"),
        pytest.param(BOOTSTRAP_SCRIPT, r"^\s*export TF_VAR_(\w+)=", id="bootstrap"),
    ],
)
def test_tf_var_inputs_match_declared_variables(
    rel_path: str, tf_var_pattern: str, project_root: Path, tf_blocks: tuple[HclBlock, ...]
) -> None:
    """Callers only set declared variables and always provide the required ones.

    Args:
        rel_path: Workflow or script relative to the repository root.
        tf_var_pattern: Regex capturing the variable name of each TF_VAR_* assignment.
        project_root: Repository root path provided by the session fixture.
        tf_blocks: Parsed Terraform blocks.
    """
    content = (project_root / rel_path).read_text(encoding="utf-8")
    provided = set(re.findall(tf_var_pattern, content, re.M))
    declared = set(_variables(tf_blocks))
    assert provided <= declared, (
        f"{rel_path} sets undeclared variables: {sorted(provided - declared)}"
    )
    assert REQUIRED_VARIABLES <= provided, (
        f"{rel_path} misses {sorted(REQUIRED_VARIABLES - provided)}"
    )
    assert "enable_cicd" in provided, f"{rel_path} must set enable_cicd explicitly"


@pytest.mark.parametrize(
    ("rel_path", "expected"),
    [
        pytest.param(
            "terraform/storage.tf",
            '"${local.project_id}-${var.resource_prefix}-tfstate"',
            id="terraform",
        ),
        pytest.param(
            TF_CI_WORKFLOW,
            '-backend-config="bucket=${GCP_PROJECT_ID}-${RESOURCE_PREFIX}-tfstate"',
            id="tf_ci",
        ),
        pytest.param(
            TF_APPLY_WORKFLOW,
            '-backend-config="bucket=${GCP_PROJECT_ID}-${RESOURCE_PREFIX}-tfstate"',
            id="tf_apply",
        ),
        pytest.param(
            BOOTSTRAP_SCRIPT, 'STATE_BUCKET="${PROJECT_ID}-${PREFIX}-tfstate"', id="bootstrap"
        ),
    ],
)
def test_state_bucket_name_is_consistent(rel_path: str, expected: str, project_root: Path) -> None:
    """Terraform, both workflows and bootstrap agree on <project>-<prefix>-tfstate.

    Args:
        rel_path: File relative to the repository root.
        expected: Exact text that builds the state bucket name in that file.
        project_root: Repository root path provided by the session fixture.
    """
    assert expected in (project_root / rel_path).read_text(encoding="utf-8")


def test_no_hardcoded_environment_identifiers(project_root: Path) -> None:
    """No project IDs, project numbers or SA emails of a real environment are hardcoded."""
    violations: list[str] = []
    for path in _scanned_files(project_root):
        rel = path.relative_to(project_root)
        content = path.read_text(encoding="utf-8")
        violations += [f"{rel}: project ID {pid}" for pid in GENERATED_PROJECT_ID.findall(content)]
        violations += [f"{rel}: {email}" for email in LITERAL_SA_EMAIL.findall(content)]
        violations += [
            f"{rel}: project number {number}"
            for number in PROJECT_NUMBER_LIKE.findall(content)
            if number != PLACEHOLDER_PROJECT_NUMBER
        ]
        if path.suffix == ".tf":
            violations += [
                f"{rel}: literal project argument"
                for _ in LITERAL_PROJECT_ARGUMENT.findall(content)
            ]
    assert not violations, "Hardcoded identifiers found:\n" + "\n".join(violations)


@pytest.mark.parametrize(
    ("pattern", "sample", "matches"),
    [
        (GENERATED_PROJECT_ID, 'project = "my-agent-482913"', True),
        (GENERATED_PROJECT_ID, "project = local.project_id", False),
        (LITERAL_PROJECT_ARGUMENT, '  project = "acme-prod"', True),
        (LITERAL_PROJECT_ARGUMENT, "  project = local.project_id", False),
        (PROJECT_NUMBER_LIKE, "projects/987654321098/locations", True),
        (PROJECT_NUMBER_LIKE, "sha256:0123456789abcdef", False),
    ],
)
def test_identifier_fingerprints_fire(pattern: re.Pattern[str], sample: str, matches: bool) -> None:
    """The fingerprints above catch real-looking identifiers and ignore variable references.

    Args:
        pattern: Fingerprint under test.
        sample: Text to scan.
        matches: Whether the fingerprint must find something in ``sample``.
    """
    assert bool(pattern.search(sample)) is matches


def test_tfvars_files_are_ignored_and_not_tracked(project_root: Path) -> None:
    """Only terraform.tfvars.example is committed; real *.tfvars files stay local."""
    gitignore = (project_root / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert {"*.tfvars", "*.tfvars.json"} <= {line.strip() for line in gitignore}

    try:
        result = subprocess.run(
            ["git", "ls-files", "--", "*.tfvars", "*.tfvars.json"],
            cwd=project_root,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        pytest.skip("git is not installed")
    if result.returncode != 0:
        pytest.skip("not inside a git work tree")
    assert result.stdout.strip() == "", f"Tracked tfvars files:\n{result.stdout}"


def test_tfvars_example_matches_declared_variables(
    project_root: Path, tf_blocks: tuple[HclBlock, ...]
) -> None:
    """The example only sets declared variables and covers every required one."""
    content = _FULL_LINE_COMMENT.sub(
        "", (project_root / TFVARS_EXAMPLE).read_text(encoding="utf-8")
    )
    keys = set(re.findall(r"^([a-z_]+)\s*=", content, re.M))
    declared = set(_variables(tf_blocks))
    assert keys <= declared, f"Undeclared keys: {sorted(keys - declared)}"
    assert REQUIRED_VARIABLES <= keys
