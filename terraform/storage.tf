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

# Terraform remote state for this agent. scripts/bootstrap_gcp.sh creates it before the first
# `terraform init` (a backend bucket must exist first) and then imports it here. The name is
# per agent so several agents can share one project without sharing state.
resource "google_storage_bucket" "tf_state" {
  name                        = "${local.project_id}-${var.resource_prefix}-tfstate"
  location                    = local.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  versioning {
    enabled = true
  }

  lifecycle {
    # This bucket holds the state of everything in this directory. To tear the stack down,
    # run `terraform state rm google_storage_bucket.tf_state` first and delete the bucket last.
    prevent_destroy = true
  }
}

# ADK artifact storage, exposed to the agent as LOGS_BUCKET_NAME (agents-cli scaffold
# convention; see .env.example).
resource "google_storage_bucket" "logs" {
  name                        = "${local.project_id}-${var.resource_prefix}-logs"
  location                    = local.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  depends_on = [google_project_service.services]
}

# Short-lived user-facing exports, exposed to the agent as EXPORTS_BUCKET_NAME.
resource "google_storage_bucket" "exports" {
  name                        = "${local.project_id}-${var.resource_prefix}-exports"
  location                    = local.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      age = 7
    }
  }

  depends_on = [google_project_service.services]
}
