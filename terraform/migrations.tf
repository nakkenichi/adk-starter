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

# State migrations.
#
# This file is intentionally empty in the template: a freshly bootstrapped project has no
# history to migrate. Whenever you rename a resource, move it into or out of `count` /
# `for_each`, or move it into a module, add a `moved {}` block here in the same pull request
# so Terraform re-addresses the existing object instead of destroying and recreating it:
#
#   moved {
#     from = google_storage_bucket.exports
#     to   = google_storage_bucket.user_exports
#   }
#
#   moved {
#     from = google_service_account.cicd_runner_sa
#     to   = google_service_account.cicd_runner_sa[0]
#   }
#
# Review the `terraform plan` comment on the pull request: it must show "has moved to" for
# every renamed resource and no unexpected destroy/create pairs. This matters most for the
# CI/CD identity (google_iam_workload_identity_pool.github_pool): a destroyed pool ID stays
# soft-deleted for 30 days and locks every GitHub workflow out of the project.
#
# Keep a `moved {}` block for as long as any deployed environment may still hold state at
# the old address.
