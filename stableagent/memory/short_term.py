"""短期 Memory facade：Ticket / Assignment scope。"""

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


class ShortTermMemory:
    def __init__(self, service: MemoryService) -> None:
        self._service = service

    def remember_ticket(
        self,
        *,
        tenant_id: str,
        ticket_id: str,
        content: str,
        origin: WriteOrigin = "worker_summary",
        classification: MemoryClassification = MemoryClassification.INTERNAL,
        confidence: float = 1.0,
        provenance: dict[str, Any] | None = None,
    ) -> MemoryRecord | None:
        return self._service.write(
            tenant_id=tenant_id,
            layer=MemoryLayer.SHORT,
            scope=MemoryScope(kind=MemoryScopeKind.TICKET, id=ticket_id),
            content=content,
            origin=origin,
            classification=classification,
            confidence=confidence,
            provenance=provenance,
        )

    def remember_assignment(
        self,
        *,
        tenant_id: str,
        assignment_id: str,
        content: str,
        origin: WriteOrigin = "worker_summary",
        classification: MemoryClassification = MemoryClassification.INTERNAL,
        confidence: float = 1.0,
        provenance: dict[str, Any] | None = None,
    ) -> MemoryRecord | None:
        return self._service.write(
            tenant_id=tenant_id,
            layer=MemoryLayer.SHORT,
            scope=MemoryScope(kind=MemoryScopeKind.ASSIGNMENT, id=assignment_id),
            content=content,
            origin=origin,
            classification=classification,
            confidence=confidence,
            provenance=provenance,
        )
