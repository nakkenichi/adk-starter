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

variable "project_id" {
  description = "Target Google Cloud project ID. Intentionally has no default."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.project_id))
    error_message = "project_id must be a 6-30 character Google Cloud project ID (lowercase letters, digits and hyphens)."
  }
}

variable "project_number" {
  description = "Target Google Cloud project number (identifies Google-managed service agents)."
  type        = string

  validation {
    condition     = can(regex("^[0-9]+$", var.project_number))
    error_message = "project_number must contain digits only."
  }
}

variable "region" {
  description = "Target Google Cloud region for all regional resources (buckets, Artifact Registry)."
  type        = string
  default     = "asia-northeast1"

  validation {
    condition     = can(regex("^[a-z]+-[a-z]+[0-9]+$", var.region))
    error_message = "region must be a Google Cloud region such as asia-northeast1 or us-central1."
  }
}

variable "github_org" {
  description = "GitHub organization (repository owner) trusted by the Workload Identity Federation provider. Required when enable_cicd = true; CI passes github.repository_owner."
  type        = string
  default     = ""

  validation {
    condition     = can(regex("^[A-Za-z0-9-]*$", var.github_org))
    error_message = "github_org must be a GitHub owner name (letters, digits and hyphens), without a slash."
  }
}

variable "github_repo" {
  description = "GitHub repository name (without the owner) allowed to impersonate the CI/CD service account. Required when enable_cicd = true; CI passes github.event.repository.name."
  type        = string
  default     = ""

  validation {
    condition     = can(regex("^[A-Za-z0-9._-]*$", var.github_repo))
    error_message = "github_repo must be the bare repository name (no owner/ prefix)."
  }
}

variable "enable_cicd" {
  description = "Enable CI/CD runner service account and GitHub Workload Identity Federation (WIF) pool."
  type        = bool
  # WARNING: setting this to false against a state that already contains the CI/CD
  # identity destroys it. Deleting google_iam_workload_identity_pool.github_pool
  # soft-deletes the pool ID for 30 days, locking every WIF workflow out of the
  # project. CI applies MUST pass TF_VAR_enable_cicd=true (see tf-apply.yml), and
  # scripts/bootstrap_gcp.sh refuses --no-cicd once the pool exists in state.
  default = false
}

variable "resource_prefix" {
  description = "Short prefix for every named resource (service accounts, WIF pool/provider, buckets, Artifact Registry repository). Must be unique per agent within the project."
  type        = string

  validation {
    # 16 characters is the maximum: the WIF provider ID "<prefix>-github-provider" must be
    # at most 32 characters and the "-github-provider" suffix already uses 16 of them.
    condition     = can(regex("^[a-z][a-z0-9-]{1,14}[a-z0-9]$", var.resource_prefix))
    error_message = "resource_prefix must be 3-16 characters of lowercase letters, digits and hyphens, start with a letter and not end with a hyphen."
  }

  validation {
    # Workload Identity pool and provider IDs may not start with the reserved "gcp-" prefix.
    condition     = !startswith(var.resource_prefix, "gcp-")
    error_message = "resource_prefix must not start with \"gcp-\" (reserved by Google for Workload Identity pool IDs)."
  }
}

variable "deployment_target" {
  description = "Where scripts/deploy.sh runs the agent: Vertex AI Agent Runtime (agent_runtime) or Cloud Run (cloud_run)."
  type        = string
  default     = "agent_runtime"

  validation {
    condition     = contains(["agent_runtime", "cloud_run"], var.deployment_target)
    error_message = "deployment_target must be agent_runtime or cloud_run."
  }
}

variable "enable_gemini_enterprise" {
  description = "Allow Gemini Enterprise to call a Cloud Run deployment by granting roles/run.invoker to the project's Discovery Engine service agent (assumes the Gemini Enterprise app lives in this project). No effect when deployment_target = agent_runtime. CI sets it when the GEMINI_ENTERPRISE_APP_ID variable is non-empty."
  type        = bool
  default     = false
}

variable "additional_services" {
  description = "Extra Google Cloud APIs to enable on top of the template baseline in services.tf, e.g. [\"bigquery.googleapis.com\"]."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for service in var.additional_services : can(regex("^[a-z0-9.-]+\\.googleapis\\.com$", service))])
    error_message = "additional_services entries must be API service names ending in .googleapis.com."
  }
}
