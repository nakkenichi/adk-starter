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

"""Persistent memory retrieval, sliding-window history compaction, and async consolidation.

Provides token-budget conversation compaction and scope-isolated memory persistence
with a remote memory bank adapter and an in-memory vector store fallback.
"""

from __future__ import annotations

import asyncio
import datetime
import functools
import importlib
import logging
import os
import re
from collections.abc import Iterable, Sequence
from typing import Final, Protocol

from app.models.enums import ObservationPriority
from app.models.schema import MemoryRecord
from app.services.redaction import PiiRedactionService

_logger: Final[logging.Logger] = logging.getLogger(__name__)
_WORD_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> set[str]:
    """Extracts lowercase alphanumeric tokens from `text`, stripping punctuation."""
    return set(_WORD_TOKEN_RE.findall(text.lower()))


def estimate_tokens(text: str) -> int:
    """Returns a fast `chars / 4` token estimate."""
    if not text:
        return 0
    return (len(text) + 3) // 4


def _build_compaction_summary(prefix_candidates: Sequence[str]) -> str:
    """Builds the deterministic summary prefix for compacted turns."""
    compacted_preview = "; ".join(m[:60] for m in prefix_candidates[:3])
    return f"[Compacted {len(prefix_candidates)} earlier turn(s): {compacted_preview}]"


def compact_conversation_window(
    messages: Sequence[str],
    *,
    max_tokens: int,
    keep_recent: int = 2,
) -> list[str]:
    """Compacts conversation history to fit `max_tokens` while preserving recent turns.

    Args:
        messages: Ordered sequence of rendered turn strings.
        max_tokens: Maximum token budget allowed for the returned window.
        keep_recent: Minimum number of most-recent messages to keep verbatim.

    Returns:
        Compacted list of messages fitting within `max_tokens` (or the recent tail).
    """
    items = [m.strip() for m in messages if m.strip()]
    if not items:
        return []

    total_tokens = sum(estimate_tokens(m) for m in items)
    if total_tokens <= max_tokens or len(items) <= keep_recent:
        return items

    tail_count = max(1, min(keep_recent, len(items)))
    retained_tail: list[str] = list(items[-tail_count:])
    tail_tokens = sum(estimate_tokens(m) for m in retained_tail)

    prefix_candidates = list(items[:-tail_count])
    kept_prefix: list[str] = []
    while prefix_candidates:
        candidate = prefix_candidates[-1]
        cand_tokens = estimate_tokens(candidate)
        remaining = prefix_candidates[:-1]
        summary_budget = estimate_tokens(_build_compaction_summary(remaining)) if remaining else 0
        if tail_tokens + cand_tokens + summary_budget > max_tokens:
            break
        kept_prefix.insert(0, prefix_candidates.pop())
        tail_tokens += cand_tokens

    if not prefix_candidates:
        return kept_prefix + retained_tail

    summary = _build_compaction_summary(prefix_candidates)
    return [summary, *kept_prefix, *retained_tail]


class PersistentMemoryBackend(Protocol):
    """Protocol for persistent user-scoped memory storage."""

    def upsert(self, record: MemoryRecord) -> None:
        """Persists a single memory record."""

    def search(self, user_id: str, query: str, *, top_k: int = 5) -> list[MemoryRecord]:
        """Retrieves up to `top_k` scope-isolated memories matching `query`."""


class _MemoriesClientProtocol(Protocol):
    """Structural protocol for remote memory bank operations."""

    def generate(self, **kwargs: object) -> object: ...
    def retrieve(self, **kwargs: object) -> Iterable[object]: ...


class _MemoryBanksNamespaceProtocol(Protocol):
    """Structural protocol for `client.memory_banks`."""

    @property
    def memories(self) -> _MemoriesClientProtocol: ...


class _AgentPlatformClientProtocol(Protocol):
    """Structural protocol for `agentplatform.Client`."""

    @property
    def memory_banks(self) -> _MemoryBanksNamespaceProtocol: ...


class InMemoryVectorStoreBackend:
    """Scope-isolated memory vector store with token-overlap similarity scoring."""

    def __init__(self) -> None:
        self._records: dict[str, list[MemoryRecord]] = {}

    def upsert(self, record: MemoryRecord) -> None:
        """Deduplicates by normalized fact text and stores the memory under `record.user_id`."""
        user_bucket = self._records.setdefault(record.user_id, [])
        normalized = record.fact.lower()
        for idx, existing in enumerate(user_bucket):
            if existing.fact.lower() == normalized:
                user_bucket[idx] = record
                return
        user_bucket.append(record)

    def search(self, user_id: str, query: str, *, top_k: int = 5) -> list[MemoryRecord]:
        """Orders user-scoped memories by descending lexical similarity to `query`."""
        candidates = list(self._records.get(user_id, ()))
        if not candidates or top_k <= 0:
            return []
        query_terms = _tokenize(query)
        if not query_terms:
            return candidates[-top_k:]

        scored: list[tuple[tuple[int, str], MemoryRecord]] = []
        for item in candidates:
            fact_terms = _tokenize(item.fact)
            overlap = len(query_terms.intersection(fact_terms))
            if overlap > 0:
                scored.append(((overlap, item.timestamp_utc), item))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [item for _, item in scored[:top_k]]


def _resolve_memory_bank_location() -> str:
    """Resolves a regional memory bank location, ignoring `'global'`."""
    for env_key in ("GOOGLE_CLOUD_AGENT_ENGINE_LOCATION", "GOOGLE_CLOUD_LOCATION"):
        candidate = os.environ.get(env_key, "").strip()
        if candidate and candidate.lower() != "global":
            return candidate
    return "us-central1"


class VertexAiMemoryBankBackend:
    """Remote memory bank backend with a local read-your-writes fallback cache."""

    def __init__(
        self,
        *,
        memory_bank_name: str,
        project_id: str,
        location: str = "us-central1",
        fallback: InMemoryVectorStoreBackend | None = None,
    ) -> None:
        self._memory_bank_name: str = memory_bank_name.strip()
        self._project_id: str = project_id.strip()
        clean_loc = location.strip()
        self._location: str = (
            clean_loc if clean_loc and clean_loc.lower() != "global" else "us-central1"
        )
        self._fallback: InMemoryVectorStoreBackend = fallback or InMemoryVectorStoreBackend()

    def _get_client(self) -> _AgentPlatformClientProtocol | None:
        """Instantiates the remote memory bank client when available."""
        ap_module = importlib.import_module("agentplatform")
        client_cls = getattr(ap_module, "Client", None)
        if not callable(client_cls):
            return None
        instance: _AgentPlatformClientProtocol = client_cls(
            project=self._project_id,
            location=self._location,
        )
        return instance

    def upsert(self, record: MemoryRecord) -> None:
        """Persists `record` into the local cache and remote memory bank."""
        self._fallback.upsert(record)
        try:
            client = self._get_client()
            if client is None:
                return
            client.memory_banks.memories.generate(
                name=self._memory_bank_name,
                scope={"user_id": record.user_id},
                direct_memories_source={
                    "direct_memories": [{"fact": record.fact}],
                },
                config={"wait_for_completion": False},
            )
        except Exception as exc:
            _logger.debug("Remote memory bank upsert fallback engaged: %s", exc)

    def search(self, user_id: str, query: str, *, top_k: int = 5) -> list[MemoryRecord]:
        """Queries the remote memory bank and merges with the local read-your-writes cache."""
        local_results = self._fallback.search(user_id, query, top_k=top_k)
        remote_results: list[MemoryRecord] = []
        try:
            client = self._get_client()
            if client is not None:
                pager = client.memory_banks.memories.retrieve(
                    name=self._memory_bank_name,
                    scope={"user_id": user_id},
                    similarity_search_params={"search_query": query, "top_k": top_k},
                )
                now_iso = datetime.datetime.now(datetime.UTC).isoformat()
                for item in pager:
                    mem = getattr(item, "memory", item)
                    fact = getattr(mem, "fact", "")
                    if isinstance(fact, str) and fact.strip():
                        remote_results.append(
                            MemoryRecord(
                                user_id=user_id,
                                session_id="vertex-memory-bank",
                                fact=fact.strip(),
                                priority=ObservationPriority.HIGH,
                                timestamp_utc=now_iso,
                            )
                        )
        except Exception as exc:
            _logger.debug("Remote memory bank search fallback engaged: %s", exc)

        if not remote_results:
            return local_results

        merged: list[MemoryRecord] = []
        seen_facts: set[str] = set()
        for record in (*local_results, *remote_results):
            norm = record.fact.lower()
            if norm not in seen_facts:
                seen_facts.add(norm)
                merged.append(record)
        return merged[:top_k]


class PersistentMemoryService:
    """Manages PII-scrubbed persistent session memories and async background consolidation."""

    def __init__(
        self,
        *,
        backend: PersistentMemoryBackend | None = None,
        redactor: PiiRedactionService | None = None,
    ) -> None:
        """Initializes the memory service.

        Args:
            backend: Persistent storage backend (`VertexAiMemoryBankBackend` when configured,
                otherwise `InMemoryVectorStoreBackend`).
            redactor: PII redaction service applied before storing any fact.
        """
        if backend is not None:
            self._backend: PersistentMemoryBackend = backend
        elif bank_name := (
            os.environ.get("MEMORY_BANK_NAME", "").strip()
            or os.environ.get("GOOGLE_CLOUD_AGENT_ENGINE_ID", "").strip()
        ):
            self._backend = VertexAiMemoryBankBackend(
                memory_bank_name=bank_name,
                project_id=os.environ.get("GOOGLE_CLOUD_PROJECT", ""),
                location=_resolve_memory_bank_location(),
            )
        else:
            self._backend = InMemoryVectorStoreBackend()
        self._redactor: PiiRedactionService = redactor or PiiRedactionService()
        self._background_tasks: set[asyncio.Task[list[MemoryRecord]]] = set()

    def build_scrubbed_record(
        self,
        user_id: str,
        session_id: str,
        raw_fact: str,
        *,
        priority: ObservationPriority = ObservationPriority.HIGH,
    ) -> MemoryRecord:
        """Scrubs PII from `raw_fact` and builds a validated `MemoryRecord` without storing it."""
        clean_fact, _ = self._redactor.redact_text(raw_fact.strip())
        return MemoryRecord(
            user_id=user_id,
            session_id=session_id,
            fact=clean_fact or "[REDACTED]",
            priority=priority,
            timestamp_utc=datetime.datetime.now(datetime.UTC).isoformat(),
        )

    def upsert_record(self, record: MemoryRecord) -> None:
        """Persists an already-validated `MemoryRecord` into the backend."""
        self._backend.upsert(record)

    def store_memory(
        self,
        user_id: str,
        session_id: str,
        raw_fact: str,
        *,
        priority: ObservationPriority = ObservationPriority.HIGH,
    ) -> MemoryRecord:
        """Scrubs PII from `raw_fact` and persists the resulting `MemoryRecord`."""
        record = self.build_scrubbed_record(
            user_id,
            session_id,
            raw_fact,
            priority=priority,
        )
        self.upsert_record(record)
        return record

    def retrieve_memories(
        self,
        user_id: str,
        query: str,
        *,
        top_k: int = 5,
    ) -> list[MemoryRecord]:
        """Retrieves up to `top_k` relevant memories for `user_id`."""
        return self._backend.search(user_id, query, top_k=top_k)

    async def consolidate_async(
        self,
        user_id: str,
        session_id: str,
        utterances: Sequence[str],
    ) -> list[MemoryRecord]:
        """Consolidates a batch of utterances into atomic, PII-scrubbed memory records."""
        created: list[MemoryRecord] = []
        for utterance in utterances:
            stripped = utterance.strip()
            if not stripped:
                continue
            record = await asyncio.to_thread(
                self.store_memory,
                user_id,
                session_id,
                stripped,
                priority=ObservationPriority.HIGH,
            )
            created.append(record)
        return created

    def _on_task_done(self, task: asyncio.Task[list[MemoryRecord]]) -> None:
        """Removes completed task from tracking set and logs any unhandled error."""
        self._background_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            _logger.warning("Background memory consolidation task failed: %s", exc)

    def schedule_background_consolidation(
        self,
        user_id: str,
        session_id: str,
        utterances: Sequence[str],
    ) -> asyncio.Task[list[MemoryRecord]]:
        """Schedules memory consolidation as a non-blocking background asyncio task."""
        task: asyncio.Task[list[MemoryRecord]] = asyncio.create_task(
            self.consolidate_async(user_id, session_id, tuple(utterances))
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._on_task_done)
        return task

    async def drain(self) -> None:
        """Waits for all in-flight background memory consolidation tasks to complete."""
        while self._background_tasks:
            pending = tuple(self._background_tasks)
            await asyncio.gather(*pending, return_exceptions=True)


@functools.cache
def get_shared_memory_service() -> PersistentMemoryService:
    """Returns the process-wide `PersistentMemoryService` shared by tools and plugins."""
    service = PersistentMemoryService()
    enterprise_fact = (
        "Tenant acct-enterprise-42 primary region is us-central1 with active-passive "
        "failover in us-east4 and Tier-3 SRE escalation routing enabled."
    )
    fintech_fact = (
        "Tenant acct-fintech-88 resolved prior incident INC-3012 by increasing Cloud SQL "
        "max_connections to 500 and enabling PgBouncer transaction pooling."
    )
    service.store_memory("acct-enterprise-42", "seed-topology", enterprise_fact)
    service.store_memory("acct-fintech-88", "seed-incident-history", fintech_fact)
    service.store_memory("eval-cli-user", "seed-topology", enterprise_fact)
    service.store_memory("eval-cli-user", "seed-incident-history", fintech_fact)
    return service
