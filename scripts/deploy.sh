#!/usr/bin/env bash
# Deploys the agent with agents-cli: the single source of truth for deployment flags.
# Called by .github/workflows/deploy.yml, `make deploy` and `scripts/bootstrap_gcp.sh --deploy`.
#
# Usage: scripts/deploy.sh [--dry-run]
#
# Required environment:
#   GCP_PROJECT_ID             Target Google Cloud project ID.
#   GCP_REGION                 Region for Agent Runtime or Cloud Run (e.g. asia-northeast1).
#   SERVICE_NAME               Agent Runtime display name or Cloud Run service name.
# Optional environment:
#   DEPLOYMENT_TARGET          agent_runtime (default) or cloud_run.
#   GCP_RUNTIME_SA             Runtime service account email.
#   LOGS_BUCKET_NAME           GCS bucket for ADK artifacts.
#   EXPORTS_BUCKET_NAME        GCS bucket for user-facing exports (app/services/storage.py).
#   IMAGE_URI                  Prebuilt container image (cloud_run only; source build if unset).
#   AGENT_VERSION              Version advertised in the A2A agent card (default: git short SHA).
#   GEMINI_ENTERPRISE_APP_ID   Gemini Enterprise app resource name; when set, the agent is
#                              (re)registered after every successful deploy (idempotent).
#   AGENT_DISPLAY_NAME         Display name in Gemini Enterprise (default: SERVICE_NAME).
#   DEPLOY_MEMORY, DEPLOY_CPU, DEPLOY_CONCURRENCY, DEPLOY_MIN_INSTANCES, DEPLOY_MAX_INSTANCES
#                              Sizing overrides (defaults: 4Gi, 2, 8, 0, 10).
set -euo pipefail

readonly AGENTS_CLI="google-agents-cli==1.5.0"
readonly AGENT_CARD_PATH="/a2a/app/.well-known/agent-card.json"

usage() {
  sed -n '2,/^set -euo pipefail$/p' "$0" | sed '$d' | sed -E 's/^# ?//'
}

dry_run=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    -n | --dry-run) dry_run=true ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      echo "error: unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

: "${GCP_PROJECT_ID:?GCP_PROJECT_ID is required}"
: "${GCP_REGION:?GCP_REGION is required}"
: "${SERVICE_NAME:?SERVICE_NAME is required}"

deployment_target="${DEPLOYMENT_TARGET:-agent_runtime}"
case "${deployment_target}" in
  agent_runtime | cloud_run) ;;
  *)
    echo "error: DEPLOYMENT_TARGET must be agent_runtime or cloud_run (got '${deployment_target}')" >&2
    exit 2
    ;;
esac

agent_version="${AGENT_VERSION:-$(git rev-parse --short HEAD 2>/dev/null || echo "0.1.0")}"

env_vars="GOOGLE_GENAI_USE_VERTEXAI=TRUE,GOOGLE_CLOUD_LOCATION=global,AGENT_VERSION=${agent_version}"
if [[ -n "${LOGS_BUCKET_NAME:-}" ]]; then
  env_vars+=",LOGS_BUCKET_NAME=${LOGS_BUCKET_NAME}"
fi
if [[ -n "${EXPORTS_BUCKET_NAME:-}" ]]; then
  env_vars+=",EXPORTS_BUCKET_NAME=${EXPORTS_BUCKET_NAME}"
fi
if [[ -n "${APP_URL:-}" ]]; then
  env_vars+=",APP_URL=${APP_URL}"
fi

deploy_args=(
  deploy
  --deployment-target "${deployment_target}"
  --project "${GCP_PROJECT_ID}"
  --region "${GCP_REGION}"
  --service-name "${SERVICE_NAME}"
  --memory "${DEPLOY_MEMORY:-4Gi}"
  --cpu "${DEPLOY_CPU:-2}"
  --concurrency "${DEPLOY_CONCURRENCY:-8}"
  --min-instances "${DEPLOY_MIN_INSTANCES:-0}"
  --max-instances "${DEPLOY_MAX_INSTANCES:-10}"
  --no-confirm-project
)
if [[ -n "${GCP_RUNTIME_SA:-}" ]]; then
  deploy_args+=(--service-account "${GCP_RUNTIME_SA}")
fi

case "${deployment_target}" in
  agent_runtime)
    # Agent Runtime builds the Dockerfile remotely from the source upload (filtered by
    # .gcloudignore), sets GOOGLE_CLOUD_PROJECT itself, and rejects --image. Do not pass
    # --agent-version: agents-cli 1.5.0 does not support it for this target.
    if [[ -n "${IMAGE_URI:-}" ]]; then
      echo "warning: IMAGE_URI is ignored for agent_runtime (it always builds from source)." >&2
    fi
    ;;
  cloud_run)
    # Cloud Run does not inject the project ID; the Gen AI SDK needs it for Vertex AI.
    env_vars+=",GOOGLE_CLOUD_PROJECT=${GCP_PROJECT_ID}"
    if [[ -n "${IMAGE_URI:-}" ]]; then
      deploy_args+=(--image "${IMAGE_URI}")
    fi
    ;;
esac
deploy_args+=(--update-env-vars "${env_vars}")

if [[ "${dry_run}" == true ]]; then
  deploy_args+=(--dry-run)
fi

echo "==> Deploying '${SERVICE_NAME}' to ${deployment_target} in ${GCP_PROJECT_ID}/${GCP_REGION}" \
  "(version ${agent_version}$([[ "${dry_run}" == true ]] && echo ", dry run"))"
uvx "${AGENTS_CLI}" "${deploy_args[@]}"

if [[ -z "${GEMINI_ENTERPRISE_APP_ID:-}" ]]; then
  echo "==> GEMINI_ENTERPRISE_APP_ID is not set; skipping Gemini Enterprise registration."
  exit 0
fi
if [[ "${dry_run}" == true ]]; then
  echo "==> Dry run: would register '${SERVICE_NAME}' in Gemini Enterprise app ${GEMINI_ENTERPRISE_APP_ID}."
  exit 0
fi

publish_args=(
  publish gemini-enterprise
  --deployment-target "${deployment_target}"
  --gemini-enterprise-app-id "${GEMINI_ENTERPRISE_APP_ID}"
  --project-id "${GCP_PROJECT_ID}"
  --display-name "${AGENT_DISPLAY_NAME:-${SERVICE_NAME}}"
)
case "${deployment_target}" in
  agent_runtime)
    # Reads the Agent Runtime resource name from deployment_metadata.json written by deploy.
    publish_args+=(--registration-type adk)
    ;;
  cloud_run)
    service_url="$(gcloud run services describe "${SERVICE_NAME}" \
      --project "${GCP_PROJECT_ID}" --region "${GCP_REGION}" --format 'value(status.url)')"
    if [[ -z "${service_url}" ]]; then
      echo "error: could not resolve the Cloud Run URL of '${SERVICE_NAME}'." >&2
      exit 1
    fi
    publish_args+=(--registration-type a2a --agent-card-url "${service_url}${AGENT_CARD_PATH}")
    ;;
esac

echo "==> Registering '${SERVICE_NAME}' in Gemini Enterprise (create or update)"
uvx "${AGENTS_CLI}" "${publish_args[@]}"
