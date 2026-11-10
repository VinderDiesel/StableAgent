"""MemoryService：统一写入/检索/删除 API，失败降级不阻断 Ticket。"""

from __future__ import annotations

from typing import Any

from stableagent.memory.errors import MemoryError, MemoryPolicyDeniedError
from stableagent.memory.long_term import LongTermMemory
from stableagent.memory.model import (
    EraseReceipt,
    MemoryAuthContext,
    MemoryClassification,
    MemoryLayer,
    MemoryRecord,
    MemoryScope,
    RetrievedMemory,
    WriteOrigin,
)
from stableagent.memory.policy import MemoryPolicy
from stableagent.memory.retrieval import LexicalRetriever, inject_into_messages
from stableagent.memory.short_term import ShortTermMemory
from stableagent.memory.store import MemoryStore
from stableagent.observability.emitter import DomainEventEmitter
from stableagent.observability.events import DomainEventKind
from stableagent.orchestration.model import Clock, IdFactory, Uuid4Id


class MemoryService:
    def __init__(
        self,
        store: MemoryStore,
        *,
        clock: Clock,
        ids: IdFactory | None = None,
        policy: MemoryPolicy | None = None,
        emitter: DomainEventEmitter | None = None,
    ) -> None:
        self._store = store
        self._clock = clock
        self._ids = ids if ids is not None else Uuid4Id()
        self._policy = policy if policy is not None else MemoryPolicy()
        self._retriever = LexicalRetriever(store)
        self._emitter = emitter
        self.short_term = ShortTermMemory(self)
        self.long_term = LongTermMemory(self)
        self.degraded_events: list[dict[str, Any]] = []

    @property
    def policy(self) -> MemoryPolicy:
        return self._policy

    @property
    def store(self) -> MemoryStore:
        return self._store

    def write(
        self,
        *,
        tenant_id: str,
        layer: MemoryLayer,
        scope: MemoryScope,
        content: str,
        origin: WriteOrigin | str,
        classification: MemoryClassification = MemoryClassification.INTERNAL,
        confidence: float = 1.0,
        provenance: dict[str, Any] | None = None,
        ticket_id: str | None = None,
    ) -> MemoryRecord | None:
        try:
            return self._write_strict(
                tenant_id=tenant_id,
                layer=layer,
                scope=scope,
                content=content,
                origin=origin,
                classification=classification,
                confidence=confidence,
                provenance=provenance,
                ticket_id=ticket_id,
            )
        except MemoryPolicyDeniedError:
            raise
        except Exception as exc:  # noqa: BLE001 — 降级契约：存储失败不阻断
            self._degrade("write", tenant_id=tenant_id, error=str(exc), ticket_id=ticket_id)
            return None

    def _write_strict(
        self,
        *,
        tenant_id: str,
        layer: MemoryLayer,
        scope: MemoryScope,
        content: str,
        origin: WriteOrigin | str,
        classification: MemoryClassification,
        confidence: float,
        provenance: dict[str, Any] | None,
        ticket_id: str | None,
    ) -> MemoryRecord:
        now = self._clock.now_ms()
        digest = self._policy.content_digest(content)
        duplicate = self._store.find_by_digest(
            tenant_id=tenant_id, layer=layer, scope=scope, content_digest=digest
        )
        existing = self._store.count_active_in_scope(
            tenant_id=tenant_id, layer=layer, scope=scope, now_ms=now
        )
        safe_content = self._policy.assert_writable(
            origin=origin,
            classification=classification,
            content=content,
            existing_count=existing,
            duplicate=duplicate,
        )
        # 脱敏后 digest 可能变化：以入库内容为准
        digest = self._policy.content_digest(safe_content)
        record = MemoryRecord(
            id=self._ids.new_id("mem"),
            tenant_id=tenant_id,
            layer=layer,
            scope_kind=scope.kind,
            scope_id=scope.id,
            origin=str(origin),
            content=safe_content,
            content_digest=digest,
            embedding_ref=None,
            created_at=now,
            updated_at=now,
            expires_at=self._policy.resolve_expires_at(layer, now_ms=now),
            confidence=confidence,
            classification=classification,
            deleted_at=None,
            provenance=dict(provenance or {}),
        )
        with self._store.transaction():
            self._store.insert_memory(record)
        self._emit(
            DomainEventKind.MEMORY_WRITTEN,
            outcome="ok",
            tenant_attrs={"memory_id": record.id, "layer": layer.value},
            ticket_id=ticket_id,
        )
        return record

    def retrieve(
        self,
        query: str,
        auth: MemoryAuthContext,
        *,
        limit: int = 5,
        token_budget: int = 512,
        min_score: float = 0.0,
        ticket_id: str | None = None,
    ) -> list[RetrievedMemory]:
        try:
            results = self._retriever.retrieve(
                query,
                auth,
                now_ms=self._clock.now_ms(),
                limit=limit,
                token_budget=token_budget,
                min_score=min_score,
            )
            self._emit(
                DomainEventKind.MEMORY_RETRIEVED,
                outcome="ok",
                tenant_attrs={"count": len(results), "query_digest": query[:64]},
                ticket_id=ticket_id,
            )
            return results
        except Exception as exc:  # noqa: BLE001
            self._degrade(
                "retrieve",
                tenant_id=auth.tenant_id,
                error=str(exc),
                ticket_id=ticket_id,
            )
            return []

    def erase(
        self,
        memory_ids: list[str],
        *,
        tenant_id: str,
        actor: str,
    ) -> EraseReceipt:
        now = self._clock.now_ms()
        with self._store.transaction():
            erased = self._store.soft_delete(
                memory_ids, tenant_id=tenant_id, deleted_at=now
            )
        receipt = EraseReceipt(
            id=self._ids.new_id("erase"),
            timestamp=now,
            actor=actor,
            tenant_id=tenant_id,
            count=len(erased),
            memory_ids=tuple(erased),
        )
        self._emit(
            DomainEventKind.MEMORY_ERASED,
            outcome="ok",
            tenant_attrs={
                "receipt_id": receipt.id,
                "count": receipt.count,
                "actor": actor,
            },
        )
        return receipt

    def erase_scope(
        self,
        *,
        tenant_id: str,
        scope: MemoryScope,
        actor: str,
        layer: MemoryLayer | None = None,
    ) -> EraseReceipt:
        now = self._clock.now_ms()
        with self._store.transaction():
            erased = self._store.soft_delete_by_scope(
                tenant_id=tenant_id, scope=scope, deleted_at=now, layer=layer
            )
        receipt = EraseReceipt(
            id=self._ids.new_id("erase"),
            timestamp=now,
            actor=actor,
            tenant_id=tenant_id,
            count=len(erased),
            memory_ids=tuple(erased),
        )
        self._emit(
            DomainEventKind.MEMORY_ERASED,
            outcome="ok",
            tenant_attrs={
                "receipt_id": receipt.id,
                "count": receipt.count,
                "actor": actor,
                "scope_kind": scope.kind.value,
                "scope_id": scope.id,
            },
        )
        return receipt

    def inject(
        self,
        messages: list[dict[str, str]],
        query: str,
        auth: MemoryAuthContext,
        *,
        limit: int = 5,
        token_budget: int = 512,
    ) -> list[dict[str, str]]:
        retrieved = self.retrieve(
            query, auth, limit=limit, token_budget=token_budget
        )
        return inject_into_messages(messages, retrieved)

    def _degrade(
        self,
        op: str,
        *,
        tenant_id: str,
        error: str,
        ticket_id: str | None = None,
    ) -> None:
        payload = {"op": op, "tenant_id": tenant_id, "error": error}
        self.degraded_events.append(payload)
        self._emit(
            DomainEventKind.MEMORY_DEGRADED,
            outcome="degraded",
            reason_code="memory.degraded",
            tenant_attrs=payload,
            ticket_id=ticket_id,
        )

    def _emit(
        self,
        kind: DomainEventKind,
        *,
        outcome: str,
        tenant_attrs: dict[str, Any],
        reason_code: str | None = None,
        ticket_id: str | None = None,
    ) -> None:
        if self._emitter is None:
            return
        self._emitter.emit_safe(
            kind,
            outcome=outcome,
            reason_code=reason_code,
            ticket_id=ticket_id,
            attrs=tenant_attrs,
        )


def is_memory_error(exc: BaseException) -> bool:
    return isinstance(exc, MemoryError)
