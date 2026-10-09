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

"""ADK tool adapters."""

from __future__ import annotations

from app.tools.example_tools import (
    create_critical_escalation_bug,
    diagnose_support_case_symptom,
    publish_high_stakes_remediation_plan,
    retrieve_customer_support_memories,
    retrieve_persistent_memories,
    run_example_task,
)

__all__ = [
    "create_critical_escalation_bug",
    "diagnose_support_case_symptom",
    "publish_high_stakes_remediation_plan",
    "retrieve_customer_support_memories",
    "retrieve_persistent_memories",
    "run_example_task",
]
