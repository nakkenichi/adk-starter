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

output "workload_identity_provider_name" {
  description = "Workload Identity Provider resource name for GitHub Actions OIDC authentication (null when enable_cicd = false)."
  value       = one(google_iam_workload_identity_pool_provider.github_provider[*].name)
}

output "cicd_runner_sa_email" {
  description = "CI/CD runner service account email (null when enable_cicd = false)."
  value       = one(google_service_account.cicd_runner_sa[*].email)
}

output "runtime_sa_email" {
  description = "Runtime service account email of the deployed agent."
  value       = google_service_account.app_sa.email
}

output "artifact_registry_repo_id" {
  description = "Artifact Registry repository ID."
  value       = google_artifact_registry_repository.repo.repository_id
}

output "artifact_registry_repo_url" {
  description = "Artifact Registry Docker repository URL."
  value       = "${local.region}-docker.pkg.dev/${local.project_id}/${google_artifact_registry_repository.repo.repository_id}"
}

output "terraform_state_bucket" {
  description = "Google Cloud Storage bucket name for Terraform remote state."
  value       = google_storage_bucket.tf_state.name
}

output "logs_bucket_name" {
  description = "Google Cloud Storage bucket for ADK artifacts (LOGS_BUCKET_NAME)."
  value       = google_storage_bucket.logs.name
}

output "exports_bucket_name" {
  description = "Google Cloud Storage bucket for short-lived user-facing exports (EXPORTS_BUCKET_NAME)."
  value       = google_storage_bucket.exports.name
}

# Consumed by scripts/bootstrap_gcp.sh, which runs `gh variable set` for every entry. The keys
# are the repository variables read by the GitHub workflows; keep both sides in sync
# (tests/unit/terraform/test_terraform_config.py pins the exact key set).
output "github_actions_variables" {
  description = "GitHub Actions repository variables for the CI/CD workflows (empty map when enable_cicd = false)."
  value = var.enable_cicd ? tomap({
    GCP_PROJECT_ID      = local.project_id
    GCP_PROJECT_NUMBER  = local.project_number
    GCP_REGION          = local.region
    RESOURCE_PREFIX     = var.resource_prefix
    DEPLOYMENT_TARGET   = var.deployment_target
    GCP_WIF_PROVIDER    = google_iam_workload_identity_pool_provider.github_provider[0].name
    GCP_CICD_SA         = google_service_account.cicd_runner_sa[0].email
    GCP_RUNTIME_SA      = google_service_account.app_sa.email
    ARTIFACT_REPO       = google_artifact_registry_repository.repo.repository_id
    LOGS_BUCKET_NAME    = google_storage_bucket.logs.name
    EXPORTS_BUCKET_NAME = google_storage_bucket.exports.name
  }) : tomap({})
}
