"""领域事件只读查询辅助；授权判定真相仍在 ``control.AuditWriter`` / authorization_audit。"""

from __future__ import annotations

from stableagent.observability.events import DomainEvent, DomainEventKind
from stableagent.observability.store import ObservabilityStore


def list_ticket_events(
    store: ObservabilityStore, ticket_id: str, *, limit: int = 200
) -> list[DomainEvent]:
    return store.list_domain_events(ticket_id=ticket_id, limit=limit)


def list_kind_events(
    store: ObservabilityStore, kind: DomainEventKind, *, limit: int = 200
) -> list[DomainEvent]:
    return store.list_domain_events(kind=kind, limit=limit)
