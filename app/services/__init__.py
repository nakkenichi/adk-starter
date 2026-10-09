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

"""ADK-free domain services, storage, observability, redaction, memory, and secrets."""

from __future__ import annotations

from app.services.example_service import ExampleService
from app.services.memory_service import (
    InMemoryVectorStoreBackend,
    PersistentMemoryBackend,
    PersistentMemoryService,
    VertexAiMemoryBankBackend,
    compact_conversation_window,
    estimate_tokens,
    get_shared_memory_service,
)
from app.services.observability import (
    StructuredAuditLogger,
    StructuredJsonFormatter,
    configure_structured_logging,
    current_trace_context,
)
from app.services.redaction import (
    DlpDeidentifyClient,
    GoogleCloudDlpClient,
    PiiRedactionService,
)
from app.services.secrets import (
    GoogleCloudSecretManagerClient,
    SecretManagerService,
    SecretVersionClient,
)
from app.services.storage import EXPORTS_BUCKET_ENV, GCSStorageService

__all__ = [
    "EXPORTS_BUCKET_ENV",
    "DlpDeidentifyClient",
    "ExampleService",
    "GCSStorageService",
    "GoogleCloudDlpClient",
    "GoogleCloudSecretManagerClient",
    "InMemoryVectorStoreBackend",
    "PersistentMemoryBackend",
    "PersistentMemoryService",
    "PiiRedactionService",
    "SecretManagerService",
    "SecretVersionClient",
    "StructuredAuditLogger",
    "StructuredJsonFormatter",
    "VertexAiMemoryBankBackend",
    "compact_conversation_window",
    "configure_structured_logging",
    "current_trace_context",
    "estimate_tokens",
    "get_shared_memory_service",
]
