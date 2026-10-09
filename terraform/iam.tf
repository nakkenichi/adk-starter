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

# IAM is managed exclusively with non-authoritative *_iam_member resources:
# they add single grants and never overwrite bindings owned by other tools or people.

# ------------------------------------------------------------------------------
# CI/CD runner service account (GitHub Actions via WIF). Gated on var.enable_cicd.
# ------------------------------------------------------------------------------

resource "google_storage_bucket_iam_member" "cicd_state_admin" {
  count  = var.enable_cicd ? 1 : 0
  bucket = google_storage_bucket.tf_state.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.cicd_runner_sa[0].email}"
}

resource "google_artifact_registry_repository_iam_member" "cicd_ar_writer" {
  count      = var.enable_cicd ? 1 : 0
  project    = local.project_id
  location   = google_artifact_registry_repository.repo.location
  repository = google_artifact_registry_repository.repo.name
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.cicd_runner_sa[0].email}"
}

# Roles required for tf-apply to manage IAM, WIF, APIs and buckets, and for deploy.yml to
# deploy and register the agent. The admin roles are not redundant with the scoped grants
# above: tf-apply also manages bucket, repository, service-account and project IAM, which
# needs storage.admin, artifactregistry.admin and iam.securityAdmin.
resource "google_project_iam_member" "cicd_sa_roles" {
  for_each = var.enable_cicd ? toset([
    "roles/aiplatform.admin",
    "roles/artifactregistry.admin",
    "roles/cloudbuild.builds.builder",
    "roles/discoveryengine.editor",
    "roles/editor",
    "roles/iam.securityAdmin",
    "roles/iam.serviceAccountTokenCreator",
    "roles/iam.workloadIdentityPoolAdmin",
    "roles/serviceusage.serviceUsageAdmin",
    "roles/storage.admin",
  ]) : toset([])

  project = local.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.cicd_runner_sa[0].email}"
}

# Lets the CI/CD SA deploy the agent with the runtime SA as its identity.
resource "google_service_account_iam_member" "cicd_actas_app_sa" {
  count              = var.enable_cicd ? 1 : 0
  service_account_id = google_service_account.app_sa.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.cicd_runner_sa[0].email}"
}

# Only workflows of this exact repository may impersonate the CI/CD SA.
resource "google_service_account_iam_member" "cicd_wif_user" {
  count              = var.enable_cicd ? 1 : 0
  service_account_id = google_service_account.cicd_runner_sa[0].name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github_pool[0].name}/attribute.repository/${local.github_org}/${local.github_repo}"
}

resource "google_service_account_iam_member" "cicd_sa_self_token_creator" {
  count              = var.enable_cicd ? 1 : 0
  service_account_id = google_service_account.cicd_runner_sa[0].name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.cicd_runner_sa[0].email}"
}

# ------------------------------------------------------------------------------
# Agent runtime service account.
# ------------------------------------------------------------------------------

resource "google_project_iam_member" "app_sa_roles" {
  for_each = toset([
    "roles/aiplatform.user",
    "roles/cloudtrace.agent",
    "roles/discoveryengine.viewer",
    "roles/dlp.user",
    "roles/logging.logWriter",
    "roles/secretmanager.secretAccessor",
    "roles/serviceusage.serviceUsageConsumer",
  ])

  project = local.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.app_sa.email}"
}

# Bucket-scoped object access instead of project-wide storage roles.
resource "google_storage_bucket_iam_member" "app_sa_exports_user" {
  bucket = google_storage_bucket.exports.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.app_sa.email}"
}

resource "google_storage_bucket_iam_member" "app_sa_logs_user" {
  bucket = google_storage_bucket.logs.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.app_sa.email}"
}

# Self token creation lets the runtime SA sign blobs (e.g. GCS signed URLs) via the IAM API.
resource "google_service_account_iam_member" "app_sa_self_token_creator" {
  service_account_id = google_service_account.app_sa.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.app_sa.email}"
}

# ------------------------------------------------------------------------------
# Gemini Enterprise -> Cloud Run. Agent Runtime registrations need no extra grant here.
# ------------------------------------------------------------------------------

# Gemini Enterprise calls a Cloud Run (A2A) agent as the project's Discovery Engine service
# agent. That identity must already exist: creating a Gemini Enterprise app provisions it,
# and scripts/bootstrap_gcp.sh runs `gcloud beta services identity create` for this case.
resource "google_project_iam_member" "gemini_enterprise_invoker" {
  count   = var.enable_gemini_enterprise && var.deployment_target == "cloud_run" ? 1 : 0
  project = local.project_id
  role    = "roles/run.invoker"
  member  = "serviceAccount:service-${local.project_number}@gcp-sa-discoveryengine.iam.gserviceaccount.com"

  depends_on = [google_project_service.services]
}
