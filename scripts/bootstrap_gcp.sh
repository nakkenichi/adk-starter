#!/usr/bin/env bash
# ==============================================================================
# Bootstrap the Google Cloud foundation of this agent repository.
# ==============================================================================
# Every resource name derives from --prefix, so several agents can share one
# Google Cloud project without sharing Terraform state.
#
#   1. Resolve the project number, enable the bootstrap APIs and create the Terraform state
#      bucket <project>-<prefix>-tfstate (versioned, uniform access, public access prevented).
#   2. terraform init, import the state bucket, terraform apply (inputs passed as TF_VAR_*).
#   3. CI/CD mode (default): publish `terraform output github_actions_variables` and
#      SERVICE_NAME as GitHub repository variables with `gh variable set`.
#   4. --deploy: deploy the agent with scripts/deploy.sh using the provisioned resources.
#   5. Print the remaining manual steps.
#
# Every step is idempotent, so the script is safe to re-run. Run with --help for usage.
# ==============================================================================

set -euo pipefail

readonly DEFAULT_REGION="asia-northeast1"
# Must match the resource_prefix validation in terraform/variables.tf (16 characters max:
# the WIF provider ID "<prefix>-github-provider" is limited to 32 characters).
readonly PREFIX_REGEX='^[a-z][a-z0-9-]{1,14}[a-z0-9]$'
readonly PROJECT_ID_REGEX='^[a-z][a-z0-9-]{4,28}[a-z0-9]$'
readonly REGION_REGEX='^[a-z]+-[a-z]+[0-9]+$'
readonly GITHUB_REPO_REGEX='^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$'
readonly SERVICE_NAME_REGEX='^[a-z]([a-z0-9-]{0,47}[a-z0-9])?$'
# APIs Terraform itself needs before it can manage the rest (terraform/services.tf).
BOOTSTRAP_APIS=(
  serviceusage.googleapis.com
  cloudresourcemanager.googleapis.com
  storage.googleapis.com
  iam.googleapis.com
)

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly REPO_ROOT

PROJECT_ID=""
PREFIX=""
REGION="${DEFAULT_REGION}"
GITHUB_REPO=""
DEPLOYMENT_TARGET="agent_runtime"
SERVICE_NAME=""
ENABLE_CICD="true"
DEPLOY="false"
ASSUME_YES="false"
# Optional: when set, Terraform grants the Cloud Run invoker role to Gemini Enterprise and
# scripts/deploy.sh registers the agent. It is also published as a GitHub variable so CI
# computes the same Terraform inputs.
GEMINI_ENTERPRISE_APP_ID="${GEMINI_ENTERPRISE_APP_ID:-}"

PROJECT_NUMBER=""
STATE_BUCKET=""
GITHUB_ORG=""
GITHUB_REPO_NAME=""
ENABLE_GEMINI_ENTERPRISE="false"
RUNTIME_SA_EMAIL=""
LOGS_BUCKET=""
EXPORTS_BUCKET=""
ARTIFACT_REPO_ID=""

if [[ -t 1 ]]; then
  C_INFO=$'\033[1;34m'
  C_WARN=$'\033[1;33m'
  C_ERROR=$'\033[1;31m'
  C_RESET=$'\033[0m'
else
  C_INFO=""
  C_WARN=""
  C_ERROR=""
  C_RESET=""
fi

info() { printf '%s==>%s %s\n' "${C_INFO}" "${C_RESET}" "$*"; }
warn() { printf '%sWARNING:%s %s\n' "${C_WARN}" "${C_RESET}" "$*" >&2; }
die() {
  printf '%sERROR:%s %s\n' "${C_ERROR}" "${C_RESET}" "$*" >&2
  exit 1
}

usage() {
  cat <<EOF
Usage: scripts/bootstrap_gcp.sh --project-id PROJECT_ID --prefix PREFIX [options]

Provision the Terraform-managed Google Cloud foundation of this agent (service accounts,
Workload Identity Federation, buckets, Artifact Registry) and wire GitHub Actions to it.

Required:
  --project-id ID              Google Cloud project ID.
  --prefix PREFIX              Resource name prefix, unique per agent within the project:
                               3-16 chars, lowercase letters, digits and hyphens, starting
                               with a letter (e.g. "invoice").

Options:
  --region REGION              Region for regional resources (default: ${DEFAULT_REGION}).
  --github-repo OWNER/NAME     GitHub repository trusted by WIF and given the repository
                               variables (default: current repository, via 'gh repo view').
  --deployment-target TARGET   agent_runtime (default) or cloud_run.
  --service-name NAME          Deployed service name (default: the repository name).
  --no-cicd                    Skip the WIF pool, CI/CD service account and GitHub variables
                               (customer handoff; 'gh' is not required). Combine with --deploy
                               for a one-shot deployment.
  --deploy                     Deploy the agent with scripts/deploy.sh after provisioning.
  -y, --yes                    Do not ask for confirmation (required without a terminal).
  -h, --help                   Show this help and exit.

Environment:
  GEMINI_ENTERPRISE_APP_ID     Optional Gemini Enterprise app resource name. Enables the
                               Cloud Run invoker grant in Terraform, is used by --deploy to
                               register the agent and is published as a GitHub variable.

Examples:
  # Developer project with GitHub CI/CD (what 'make bootstrap' runs):
  scripts/bootstrap_gcp.sh --project-id my-project --prefix invoice

  # Customer handoff: provision and deploy without GitHub CI/CD:
  scripts/bootstrap_gcp.sh --project-id customer-project --prefix invoice --no-cicd --deploy
EOF
}

require_value() {
  if [[ $# -lt 2 || -z "$2" || "$2" == -* ]]; then
    die "Option $1 requires a value (see --help)."
  fi
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --project-id)
        require_value "$@"
        PROJECT_ID="$2"
        shift 2
        ;;
      --project-id=*)
        PROJECT_ID="${1#*=}"
        shift
        ;;
      --prefix)
        require_value "$@"
        PREFIX="$2"
        shift 2
        ;;
      --prefix=*)
        PREFIX="${1#*=}"
        shift
        ;;
      --region)
        require_value "$@"
        REGION="$2"
        shift 2
        ;;
      --region=*)
        REGION="${1#*=}"
        shift
        ;;
      --github-repo)
        require_value "$@"
        GITHUB_REPO="$2"
        shift 2
        ;;
      --github-repo=*)
        GITHUB_REPO="${1#*=}"
        shift
        ;;
      --deployment-target)
        require_value "$@"
        DEPLOYMENT_TARGET="$2"
        shift 2
        ;;
      --deployment-target=*)
        DEPLOYMENT_TARGET="${1#*=}"
        shift
        ;;
      --service-name)
        require_value "$@"
        SERVICE_NAME="$2"
        shift 2
        ;;
      --service-name=*)
        SERVICE_NAME="${1#*=}"
        shift
        ;;
      --no-cicd)
        ENABLE_CICD="false"
        shift
        ;;
      --deploy)
        DEPLOY="true"
        shift
        ;;
      -y | --yes)
        ASSUME_YES="true"
        shift
        ;;
      -h | --help)
        usage
        exit 0
        ;;
      *)
        die "Unknown option: $1 (see --help)."
        ;;
    esac
  done
}

validate_inputs() {
  if [[ -z "${PROJECT_ID}" || -z "${PREFIX}" ]]; then
    die "--project-id and --prefix are required (see --help)."
  fi
  if [[ ! "${PROJECT_ID}" =~ ${PROJECT_ID_REGEX} ]]; then
    die "Invalid --project-id '${PROJECT_ID}': expected a 6-30 character Google Cloud project ID."
  fi
  if [[ ! "${PREFIX}" =~ ${PREFIX_REGEX} ]]; then
    die "Invalid --prefix '${PREFIX}': use 3-16 lowercase letters, digits or hyphens, starting with a letter and not ending with a hyphen."
  fi
  if [[ "${PREFIX}" == gcp-* ]]; then
    die "Invalid --prefix '${PREFIX}': the 'gcp-' prefix is reserved for Workload Identity pool IDs."
  fi
  if [[ ! "${REGION}" =~ ${REGION_REGEX} ]]; then
    die "Invalid --region '${REGION}': expected a region such as ${DEFAULT_REGION}."
  fi
  case "${DEPLOYMENT_TARGET}" in
    agent_runtime | cloud_run) ;;
    *) die "Invalid --deployment-target '${DEPLOYMENT_TARGET}': use agent_runtime or cloud_run." ;;
  esac
  if [[ -n "${GITHUB_REPO}" && ! "${GITHUB_REPO}" =~ ${GITHUB_REPO_REGEX} ]]; then
    die "Invalid --github-repo '${GITHUB_REPO}': expected OWNER/NAME."
  fi
  if [[ "${ASSUME_YES}" != "true" && ! -t 0 ]]; then
    die "No terminal to confirm on; re-run with --yes to approve non-interactively."
  fi
  STATE_BUCKET="${PROJECT_ID}-${PREFIX}-tfstate"
}

require_cli() {
  if ! command -v "$1" >/dev/null 2>&1; then
    die "Required CLI '$1' was not found on PATH. $2"
  fi
}

check_prerequisites() {
  require_cli gcloud "Install the Google Cloud CLI: https://cloud.google.com/sdk/docs/install"
  require_cli terraform "Install Terraform >= 1.5: https://developer.hashicorp.com/terraform/install"
  require_cli python3 "Install Python 3."
  if [[ "${ENABLE_CICD}" == "true" ]]; then
    require_cli gh "Install the GitHub CLI (https://cli.github.com) or pass --no-cicd."
    if ! gh auth token >/dev/null 2>&1; then
      die "The GitHub CLI is not authenticated; run 'gh auth login' (or pass --no-cicd)."
    fi
  fi
  if [[ "${DEPLOY}" == "true" && ! -x scripts/deploy.sh ]]; then
    die "--deploy needs an executable scripts/deploy.sh."
  fi

  local account
  account="$(gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null || true)"
  if [[ -z "${account}" ]]; then
    die "No active gcloud account; run 'gcloud auth login'."
  fi

  # Variable files take precedence over TF_VAR_* and would silently override this script's
  # inputs (e.g. enable_cicd), so refuse to run while any of them exists.
  local tfvars_file
  for tfvars_file in terraform/terraform.tfvars terraform/terraform.tfvars.json \
    terraform/*.auto.tfvars terraform/*.auto.tfvars.json; do
    if [[ -e "${tfvars_file}" ]]; then
      die "${tfvars_file} would override the inputs of this script; move it aside and re-run."
    fi
  done
}

ensure_terraform_credentials() {
  if [[ -n "${GOOGLE_OAUTH_ACCESS_TOKEN:-}${GOOGLE_CREDENTIALS:-}${GOOGLE_APPLICATION_CREDENTIALS:-}" ]]; then
    return 0
  fi
  if gcloud auth application-default print-access-token >/dev/null 2>&1; then
    return 0
  fi
  if [[ "${DEPLOY}" == "true" ]]; then
    die "Application Default Credentials are required when --deploy is set; run 'gcloud auth application-default login'."
  fi
  warn "Application Default Credentials are not set up; Terraform will use a short-lived token of the active gcloud account ('gcloud auth application-default login' avoids this)."
  GOOGLE_OAUTH_ACCESS_TOKEN="$(gcloud auth print-access-token)"
  export GOOGLE_OAUTH_ACCESS_TOKEN
}

resolve_github_repo() {
  if [[ -z "${GITHUB_REPO}" ]]; then
    if [[ "${ENABLE_CICD}" == "true" ]]; then
      GITHUB_REPO="$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null || true)"
      if [[ -z "${GITHUB_REPO}" ]]; then
        die "Could not detect the GitHub repository with 'gh repo view'; pass --github-repo OWNER/NAME."
      fi
    elif command -v gh >/dev/null 2>&1; then
      # Best effort: only used for the default service name in --no-cicd mode.
      GITHUB_REPO="$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null || true)"
    fi
  fi
  if [[ -n "${GITHUB_REPO}" ]]; then
    if [[ ! "${GITHUB_REPO}" =~ ${GITHUB_REPO_REGEX} ]]; then
      die "Unexpected GitHub repository '${GITHUB_REPO}': expected OWNER/NAME."
    fi
    GITHUB_ORG="${GITHUB_REPO%%/*}"
    GITHUB_REPO_NAME="${GITHUB_REPO#*/}"
  fi
}

resolve_service_name() {
  if [[ -z "${SERVICE_NAME}" ]]; then
    local base_name="${GITHUB_REPO_NAME}"
    if [[ -z "${base_name}" ]]; then
      base_name="$(basename "${REPO_ROOT}")"
    fi
    # Cloud Run service names only allow lowercase letters, digits and hyphens.
    SERVICE_NAME="$(printf '%s' "${base_name}" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9-' '-' | sed -E 's/^-+//; s/-+$//')"
  fi
  if [[ ! "${SERVICE_NAME}" =~ ${SERVICE_NAME_REGEX} ]]; then
    die "Invalid service name '${SERVICE_NAME}': use at most 49 lowercase letters, digits or hyphens, starting with a letter; pass --service-name."
  fi
}

resolve_gemini_enterprise() {
  if [[ -z "${GEMINI_ENTERPRISE_APP_ID}" && "${ENABLE_CICD}" == "true" ]]; then
    # CI derives TF_VAR_enable_gemini_enterprise from this repository variable; reuse it so a
    # local re-run does not plan away the invoker grant that CI keeps applying.
    GEMINI_ENTERPRISE_APP_ID="$(gh variable get GEMINI_ENTERPRISE_APP_ID --repo "${GITHUB_REPO}" 2>/dev/null || true)"
  fi
  if [[ -n "${GEMINI_ENTERPRISE_APP_ID}" ]]; then
    ENABLE_GEMINI_ENTERPRISE="true"
    # scripts/deploy.sh reads it from the environment to register the agent.
    export GEMINI_ENTERPRISE_APP_ID
  fi
}

print_summary() {
  local cicd_mode="enabled (WIF + GitHub repository variables)"
  if [[ "${ENABLE_CICD}" != "true" ]]; then
    cicd_mode="disabled (--no-cicd)"
  fi
  local gemini_mode="disabled (GEMINI_ENTERPRISE_APP_ID not set)"
  if [[ "${ENABLE_GEMINI_ENTERPRISE}" == "true" ]]; then
    gemini_mode="${GEMINI_ENTERPRISE_APP_ID}"
  fi
  printf '\nBootstrap configuration\n'
  printf '  %-20s %s\n' \
    "Project:" "${PROJECT_ID}" \
    "Resource prefix:" "${PREFIX}" \
    "Region:" "${REGION}" \
    "Deployment target:" "${DEPLOYMENT_TARGET}" \
    "Service name:" "${SERVICE_NAME}" \
    "GitHub repository:" "${GITHUB_REPO:-(none)}" \
    "CI/CD:" "${cicd_mode}" \
    "Gemini Enterprise:" "${gemini_mode}" \
    "Deploy afterwards:" "${DEPLOY}" \
    "State bucket:" "gs://${STATE_BUCKET}"
  printf '\n'
}

confirm() {
  if [[ "${ASSUME_YES}" == "true" ]]; then
    return 0
  fi
  local reply
  read -r -p "$1 [y/N] " reply
  case "${reply}" in
    [yY] | [yY][eE][sS]) ;;
    *) die "Aborted by user." ;;
  esac
}

ensure_discovery_engine_agent() {
  info "Ensuring the Discovery Engine service agent exists (Gemini Enterprise invoker grant)..."
  gcloud services enable discoveryengine.googleapis.com --project="${PROJECT_ID}"
  if ! gcloud beta services identity create --service=discoveryengine.googleapis.com \
    --project="${PROJECT_ID}" >/dev/null; then
    warn "Could not create the Discovery Engine service agent. terraform apply fails on gemini_enterprise_invoker until it exists (creating the Gemini Enterprise app also provisions it)."
  fi
}

bootstrap_project() {
  info "[1/5] Resolving ${PROJECT_ID} and preparing the Terraform state bucket..."
  PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
  if [[ -z "${PROJECT_NUMBER}" ]]; then
    die "Could not resolve the project number of ${PROJECT_ID}."
  fi
  info "Project number: ${PROJECT_NUMBER}"

  gcloud services enable "${BOOTSTRAP_APIS[@]}" --project="${PROJECT_ID}"

  if gcloud storage buckets describe "gs://${STATE_BUCKET}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    info "State bucket gs://${STATE_BUCKET} already exists."
  else
    info "Creating state bucket gs://${STATE_BUCKET}..."
    gcloud storage buckets create "gs://${STATE_BUCKET}" \
      --project="${PROJECT_ID}" \
      --location="${REGION}" \
      --uniform-bucket-level-access \
      --public-access-prevention
    gcloud storage buckets update "gs://${STATE_BUCKET}" --versioning
  fi

  if [[ "${ENABLE_GEMINI_ENTERPRISE}" == "true" && "${DEPLOYMENT_TARGET}" == "cloud_run" ]]; then
    ensure_discovery_engine_agent
  fi
}

state_has() {
  terraform -chdir=terraform state show "$1" >/dev/null 2>&1
}

terraform_apply() {
  info "[2/5] Applying Terraform (enable_cicd=${ENABLE_CICD})..."
  export TF_VAR_project_id="${PROJECT_ID}"
  export TF_VAR_project_number="${PROJECT_NUMBER}"
  export TF_VAR_region="${REGION}"
  export TF_VAR_resource_prefix="${PREFIX}"
  export TF_VAR_github_org="${GITHUB_ORG}"
  export TF_VAR_github_repo="${GITHUB_REPO_NAME}"
  export TF_VAR_deployment_target="${DEPLOYMENT_TARGET}"
  export TF_VAR_enable_gemini_enterprise="${ENABLE_GEMINI_ENTERPRISE}"
  export TF_VAR_enable_cicd="${ENABLE_CICD}"

  terraform -chdir=terraform init -reconfigure -input=false -backend-config="bucket=${STATE_BUCKET}"

  if [[ "${ENABLE_CICD}" != "true" ]] && state_has 'google_iam_workload_identity_pool.github_pool[0]'; then
    die "The Terraform state already contains the CI/CD identity; --no-cicd would destroy the WIF pool (its ID stays soft-deleted for 30 days). Re-run without --no-cicd."
  fi
  if ! state_has google_storage_bucket.tf_state; then
    info "Importing gs://${STATE_BUCKET} into the Terraform state..."
    terraform -chdir=terraform import -input=false google_storage_bucket.tf_state "${STATE_BUCKET}"
  fi

  if [[ "${ASSUME_YES}" == "true" ]]; then
    terraform -chdir=terraform apply -input=false -auto-approve
  else
    # Terraform shows the plan and asks for an explicit "yes".
    terraform -chdir=terraform apply
  fi
}

tf_output() {
  terraform -chdir=terraform output -raw "$1"
}

load_outputs() {
  RUNTIME_SA_EMAIL="$(tf_output runtime_sa_email)"
  LOGS_BUCKET="$(tf_output logs_bucket_name)"
  EXPORTS_BUCKET="$(tf_output exports_bucket_name)"
  ARTIFACT_REPO_ID="$(tf_output artifact_registry_repo_id)"
}

publish_github_variables() {
  if [[ "${ENABLE_CICD}" != "true" ]]; then
    info "[3/5] Skipping GitHub repository variables (--no-cicd)."
    return 0
  fi
  info "[3/5] Setting GitHub repository variables on ${GITHUB_REPO}..."

  local outputs_json variable_lines key value
  outputs_json="$(terraform -chdir=terraform output -json github_actions_variables)"
  variable_lines="$(
    python3 - "${outputs_json}" <<'PY'
import json
import re
import sys

variables = json.loads(sys.argv[1])
if not isinstance(variables, dict) or not variables:
    sys.exit("github_actions_variables is empty; was Terraform applied with enable_cicd=true?")
for key in sorted(variables):
    value = variables[key]
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
        sys.exit(f"Refusing to publish an invalid GitHub variable name: {key!r}")
    if not isinstance(value, str) or not value or any(ch in value for ch in "\t\r\n"):
        sys.exit(f"Refusing to publish an empty or multi-line value for {key}")
    print(f"{key}\t{value}")
PY
  )"

  while IFS=$'\t' read -r key value; do
    if [[ -z "${key}" ]]; then
      continue
    fi
    gh variable set "${key}" --repo "${GITHUB_REPO}" --body "${value}" </dev/null
  done <<<"${variable_lines}"

  gh variable set SERVICE_NAME --repo "${GITHUB_REPO}" --body "${SERVICE_NAME}" </dev/null
  if [[ -n "${GEMINI_ENTERPRISE_APP_ID}" ]]; then
    gh variable set GEMINI_ENTERPRISE_APP_ID --repo "${GITHUB_REPO}" \
      --body "${GEMINI_ENTERPRISE_APP_ID}" </dev/null
  fi
}

deploy_agent() {
  if [[ "${DEPLOY}" != "true" ]]; then
    info "[4/5] Skipping deployment (pass --deploy to deploy now)."
    return 0
  fi
  info "[4/5] Deploying ${SERVICE_NAME} to ${DEPLOYMENT_TARGET} with scripts/deploy.sh..."
  # scripts/deploy.sh is configured exclusively through these environment variables.
  GCP_PROJECT_ID="${PROJECT_ID}" \
    GCP_REGION="${REGION}" \
    SERVICE_NAME="${SERVICE_NAME}" \
    DEPLOYMENT_TARGET="${DEPLOYMENT_TARGET}" \
    GCP_RUNTIME_SA="${RUNTIME_SA_EMAIL}" \
    LOGS_BUCKET_NAME="${LOGS_BUCKET}" \
    EXPORTS_BUCKET_NAME="${EXPORTS_BUCKET}" \
    ARTIFACT_REPO="${ARTIFACT_REPO_ID}" \
    ./scripts/deploy.sh
}

print_next_steps() {
  info "[5/5] Bootstrap complete. Next steps:"
  if [[ "${ENABLE_CICD}" == "true" ]]; then
    cat <<EOF
  1. Enable the Claude model used by the AI PR review agent (REVIEW_MODEL, see
     .github/workflows/review-agent.yml) in Vertex AI Model Garden:
       https://console.cloud.google.com/vertex-ai/model-garden?project=${PROJECT_ID}
  2. Optional repository variables:
       gh variable set GEMINI_ENTERPRISE_APP_ID --repo ${GITHUB_REPO} \\
         --body "projects/${PROJECT_NUMBER}/locations/global/collections/default_collection/engines/<ENGINE_ID>"
       gh variable set REVIEW_MODEL --repo ${GITHUB_REPO} --body "<vertex-ai-claude-model-id>"
     After setting GEMINI_ENTERPRISE_APP_ID, re-run this script so Terraform also applies
     the Gemini Enterprise invoker grant (cloud_run only).
  3. Protect main (Settings > Rules > Rulesets): require pull requests with a human approval
     and the "Fast Offline CI" and "Shell & Workflow Lint" checks. The other workflows are
     path-filtered, so requiring them would block pull requests that never trigger them.
  4. Deploy by merging to main (deploy.yml), or locally with 'make deploy'.

New WIF and IAM bindings can take a few minutes to propagate before the first workflow run.
EOF
  else
    cat <<EOF
  1. Deploy (skip if you passed --deploy) by re-running with --deploy, or with:
       export GCP_PROJECT_ID=${PROJECT_ID} GCP_REGION=${REGION} \\
         SERVICE_NAME=${SERVICE_NAME} DEPLOYMENT_TARGET=${DEPLOYMENT_TARGET} \\
         GCP_RUNTIME_SA=${RUNTIME_SA_EMAIL} ARTIFACT_REPO=${ARTIFACT_REPO_ID} \\
         LOGS_BUCKET_NAME=${LOGS_BUCKET} EXPORTS_BUCKET_NAME=${EXPORTS_BUCKET}
       ./scripts/deploy.sh
  2. Optional: export GEMINI_ENTERPRISE_APP_ID (the Gemini Enterprise app resource name,
     projects/${PROJECT_NUMBER}/locations/global/collections/default_collection/engines/<ENGINE_ID>)
     before re-running with --deploy to register the agent in Gemini Enterprise.
  3. To add GitHub CI/CD later, re-run without --no-cicd.
EOF
  fi
}

main() {
  parse_args "$@"
  validate_inputs
  cd "${REPO_ROOT}"
  check_prerequisites
  resolve_github_repo
  resolve_service_name
  resolve_gemini_enterprise
  print_summary
  confirm "Provision these resources in ${PROJECT_ID}?"
  ensure_terraform_credentials
  bootstrap_project
  terraform_apply
  load_outputs
  publish_github_variables
  deploy_agent
  print_next_steps
}

main "$@"
