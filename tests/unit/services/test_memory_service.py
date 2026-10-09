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

"""Unit tests for PersistentMemoryService, sliding-window compaction, and async consolidation."""

from __future__ import annotations

import pytest

from app.services.memory_service import (
    PersistentMemoryService,
    VertexAiMemoryBankBackend,
    compact_conversation_window,
    estimate_tokens,
)
from app.services.redaction import REDACTED_EMAIL


@pytest.mark.parametrize(
    ("messages", "max_tokens", "keep_recent", "expected_first_prefix"),
    [
        pytest.param(
            ["short turn 1", "short turn 2"],
            500,
            2,
            "short turn 1",
            id="within_budget_unmodified",
        ),
        pytest.param(
            [
                "turn one with detailed context " * 8,
                "turn two with detailed context " * 8,
                "turn three recent",
                "turn four recent",
            ],
            40,
            2,
            "[Compacted ",
            id="over_budget_summarizes_prefix",
        ),
    ],
)
def test_compact_conversation_window(
    messages: list[str],
    max_tokens: int,
    keep_recent: int,
    expected_first_prefix: str,
) -> None:
    compacted = compact_conversation_window(
        messages,
        max_tokens=max_tokens,
        keep_recent=keep_recent,
    )

    assert compacted[0].startswith(expected_first_prefix)
    assert compacted[-1] == messages[-1]
    assert estimate_tokens("hello world") >= 2


async def test_persistent_memory_service_isolates_users_scrubs_pii_and_consolidates_async() -> None:
    service = PersistentMemoryService()

    task = service.schedule_background_consolidation(
        "user-a",
        "sess-1",
        [
            "User prefers region asia-northeast1 and email alice@example.com",
            "Primary cluster is gke-prod-apne1",
        ],
    )
    await service.drain()
    records = await task

    assert len(records) == 2
    assert REDACTED_EMAIL in records[0].fact
    assert "alice@example.com" not in records[0].fact

    user_a_region_hits = service.retrieve_memories("user-a", "region asia-northeast1?", top_k=2)
    user_a_all_hits = service.retrieve_memories(
        "user-a", "region asia-northeast1 cluster gke-prod-apne1", top_k=2
    )
    user_b_hits = service.retrieve_memories("user-b", "region asia-northeast1", top_k=2)

    assert len(user_a_region_hits) == 1
    assert "asia-northeast1" in user_a_region_hits[0].fact
    assert len(user_a_all_hits) == 2
    assert user_b_hits == []


def test_vertex_ai_memory_bank_backend_syncs_and_searches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generated_calls: list[dict[str, object]] = []

    class _FakeMemories:
        def generate(self, **kwargs: object) -> None:
            generated_calls.append(kwargs)

        def retrieve(self, **kwargs: object) -> list[object]:
            del kwargs
            return []

    class _FakeMemoryBanks:
        memories = _FakeMemories()

    class _FakeClient:
        def __init__(self, *, project: str, location: str) -> None:
            assert project == "demo-project"
            assert location == "us-central1"
            self.memory_banks = _FakeMemoryBanks()

    class _FakeAgentPlatformModule:
        Client = _FakeClient

    monkeypatch.setattr(
        "app.services.memory_service.importlib.import_module",
        lambda name: _FakeAgentPlatformModule() if name == "agentplatform" else None,
    )
    backend = VertexAiMemoryBankBackend(
        memory_bank_name="projects/demo-project/locations/us-central1/memoryBanks/mb-1",
        project_id="demo-project",
    )
    service = PersistentMemoryService(backend=backend)
    service.store_memory("user-a", "sess-1", "Cluster gke-prod-1 is primary")

    assert len(generated_calls) == 1
    hits = service.retrieve_memories("user-a", "gke-prod-1", top_k=1)
    assert len(hits) == 1
    assert "gke-prod-1" in hits[0].fact
