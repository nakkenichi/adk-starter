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

resource "google_service_account" "cicd_runner_sa" {
  count        = var.enable_cicd ? 1 : 0
  project      = local.project_id
  account_id   = "${var.resource_prefix}-cicd-sa"
  display_name = "${var.resource_prefix} CI/CD runner"
  description  = "Service account impersonated by GitHub Actions through Workload Identity Federation"

  depends_on = [google_project_service.services]
}

resource "google_service_account" "app_sa" {
  project      = local.project_id
  account_id   = "${var.resource_prefix}-runtime-sa"
  display_name = "${var.resource_prefix} agent runtime"
  description  = "Runtime service account for the deployed agent (Vertex AI Agent Runtime or Cloud Run)"

  depends_on = [google_project_service.services]
}
