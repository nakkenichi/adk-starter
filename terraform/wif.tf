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

resource "google_iam_workload_identity_pool" "github_pool" {
  count                     = var.enable_cicd ? 1 : 0
  project                   = local.project_id
  workload_identity_pool_id = "${var.resource_prefix}-github-pool"
  display_name              = "${var.resource_prefix} GitHub Actions"
  description               = "Workload Identity Pool for GitHub Actions CI/CD"
  disabled                  = false

  depends_on = [google_project_service.services]
}

resource "google_iam_workload_identity_pool_provider" "github_provider" {
  count                              = var.enable_cicd ? 1 : 0
  project                            = local.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.github_pool[0].workload_identity_pool_id
  workload_identity_pool_provider_id = "${var.resource_prefix}-github-provider"
  display_name                       = "${var.resource_prefix} GitHub OIDC"
  description                        = "Workload Identity Provider for GitHub Actions OIDC"
  disabled                           = false

  attribute_mapping = {
    "google.subject"             = "assertion.sub"
    "attribute.repository"       = "assertion.repository"
    "attribute.repository_owner" = "assertion.repository_owner"
  }

  # The pool only accepts tokens minted for repositories of this owner; the repository-scoped
  # principalSet in iam.tf (cicd_wif_user) further limits who may impersonate the CI/CD SA.
  attribute_condition = "assertion.repository_owner == '${local.github_org}'"

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }

  lifecycle {
    precondition {
      condition     = local.github_org != "" && local.github_repo != ""
      error_message = "github_org and github_repo must be set when enable_cicd = true (CI passes them from the GitHub context; scripts/bootstrap_gcp.sh resolves them with gh)."
    }
  }
}
