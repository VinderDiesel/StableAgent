"""长期 Memory facade：tenant / repository scope。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from stableagent.memory.model import (
    MemoryClassification,
    MemoryLayer,
    MemoryRecord,
    MemoryScope,
    MemoryScopeKind,
    WriteOrigin,
)

if TYPE_CHECKING:
    from stableagent.memory.service import MemoryService


class LongTermMemory:
    def __init__(self, service: MemoryService) -> None:
        self._service = service

    def remember_tenant(
        self,
        *,
        tenant_id: str,
        content: str,
        origin: WriteOrigin = "verified_fact",
        classification: MemoryClassification = MemoryClassification.INTERNAL,
        confidence: float = 1.0,
        provenance: dict[str, Any] | None = None,
    ) -> MemoryRecord | None:
        return self._service.write(
            tenant_id=tenant_id,
            layer=MemoryLayer.LONG,
            scope=MemoryScope(kind=MemoryScopeKind.TENANT, id=tenant_id),
            content=content,
            origin=origin,
            classification=classification,
            confidence=confidence,
            provenance=provenance,
        )

    def remember_repository(
        self,
        *,
        tenant_id: str,
        repository_id: str,
        content: str,
        origin: WriteOrigin = "verified_fact",
        classification: MemoryClassification = MemoryClassification.INTERNAL,
        confidence: float = 1.0,
        provenance: dict[str, Any] | None = None,
    ) -> MemoryRecord | None:
        return self._service.write(
            tenant_id=tenant_id,
            layer=MemoryLayer.LONG,
            scope=MemoryScope(kind=MemoryScopeKind.REPOSITORY, id=repository_id),
            content=content,
            origin=origin,
            classification=classification,
            confidence=confidence,
            provenance=provenance,
        )
