"""P7 M4：MemoryService 写入/删除/降级/inject + facade。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from stableagent.memory.errors import MemoryPolicyDeniedError
from stableagent.memory.model import (
    MemoryAuthContext,
    MemoryClassification,
    MemoryLayer,
    MemoryScope,
    MemoryScopeKind,
)
from stableagent.memory.policy import MemoryPolicy
from stableagent.memory.service import MemoryService
from stableagent.memory.store import MemoryStore
from stableagent.observability.emitter import DomainEventEmitter
from stableagent.observability.events import DomainEventKind
from tests.support import FixedClock, SequenceIds


def _svc(tmp_path: Path, clock: FixedClock | None = None) -> MemoryService:
    clk = clock or FixedClock(1_000)
    store = MemoryStore(tmp_path / "m.db", clock=clk)
    emitter = DomainEventEmitter(store, clock=clk, ids=SequenceIds())
    return MemoryService(
        store,
        clock=clk,
        ids=SequenceIds(),
        policy=MemoryPolicy(short_ttl_ms=5_000),
        emitter=emitter,
    )


def test_short_and_long_write_and_retrieve(tmp_path: Path) -> None:
    svc = _svc(tmp_path)
    short = svc.short_term.remember_ticket(
        tenant_id="tenant-a",
        ticket_id="tk-1",
        content="Prefer quiet notifications for python review",
    )
    long = svc.long_term.remember_repository(
        tenant_id="tenant-a",
        repository_id="repo-a",
        content="Prefer ruff and mypy for python style gates",
    )
    assert short is not None and long is not None
    auth = MemoryAuthContext(
        tenant_id="tenant-a",
        allowed_scopes=frozenset(
            {
                MemoryScope(kind=MemoryScopeKind.TICKET, id="tk-1"),
                MemoryScope(kind=MemoryScopeKind.REPOSITORY, id="repo-a"),
            }
        ),
    )
    hits = svc.retrieve("python ruff style", auth, min_score=0.1)
    ids = [h.record.id for h in hits]
    assert long.id in ids
    events = svc.store.list_domain_events(kind=DomainEventKind.MEMORY_WRITTEN)
    assert len(events) >= 2


def test_policy_denied_propagates(tmp_path: Path) -> None:
    svc = _svc(tmp_path)
    with pytest.raises(MemoryPolicyDeniedError):
        svc.write(
            tenant_id="tenant-a",
            layer=MemoryLayer.LONG,
            scope=MemoryScope(kind=MemoryScopeKind.TENANT, id="tenant-a"),
            content="x",
            origin="not-allowed-origin",
            classification=MemoryClassification.INTERNAL,
        )


def test_erase_receipt_and_hidden(tmp_path: Path) -> None:
    svc = _svc(tmp_path)
    rec = svc.long_term.remember_tenant(
        tenant_id="tenant-a", content="Durable outbox idempotency keys"
    )
    assert rec is not None
    receipt = svc.erase([rec.id], tenant_id="tenant-a", actor="op-1")
    assert receipt.count == 1
    assert receipt.memory_ids == (rec.id,)
    auth = MemoryAuthContext(
        tenant_id="tenant-a",
        allowed_scopes=frozenset(
            {MemoryScope(kind=MemoryScopeKind.TENANT, id="tenant-a")}
        ),
        layers=frozenset({MemoryLayer.LONG}),
    )
    assert svc.retrieve("outbox idempotency", auth) == []
    erased_events = svc.store.list_domain_events(kind=DomainEventKind.MEMORY_ERASED)
    assert len(erased_events) == 1


def test_retrieve_degrades_on_store_failure(tmp_path: Path) -> None:
    clock = FixedClock(1_000)
    store = MemoryStore(tmp_path / "m.db", clock=clock)
    svc = MemoryService(store, clock=clock, ids=SequenceIds())

    def boom(*_a: Any, **_k: Any) -> list[Any]:
        raise RuntimeError("index down")

    svc._retriever.retrieve = boom  # type: ignore[method-assign]  # noqa: SLF001
    auth = MemoryAuthContext(
        tenant_id="tenant-a",
        allowed_scopes=frozenset(
            {MemoryScope(kind=MemoryScopeKind.TICKET, id="tk-1")}
        ),
    )
    assert svc.retrieve("anything", auth) == []
    assert svc.degraded_events
    assert svc.degraded_events[0]["op"] == "retrieve"


def test_inject_prepends_system_block(tmp_path: Path) -> None:
    svc = _svc(tmp_path)
    svc.long_term.remember_repository(
        tenant_id="tenant-a",
        repository_id="repo-a",
        content="Prefer ruff and mypy for python style gates",
    )
    auth = MemoryAuthContext(
        tenant_id="tenant-a",
        allowed_scopes=frozenset(
            {MemoryScope(kind=MemoryScopeKind.REPOSITORY, id="repo-a")}
        ),
        layers=frozenset({MemoryLayer.LONG}),
    )
    messages = [{"role": "user", "content": "fix style?"}]
    out = svc.inject(messages, "python ruff style", auth)
    assert out[0]["role"] == "system"
    assert "Relevant memory" in out[0]["content"]
    assert out[1]["role"] == "user"


def test_assignment_facade_erase_scope_and_write_degrade(tmp_path: Path) -> None:
    from stableagent.memory.service import is_memory_error

    svc = _svc(tmp_path)
    rec = svc.short_term.remember_assignment(
        tenant_id="tenant-a",
        assignment_id="as-1",
        content="Assignment prefers quiet python notifications",
    )
    assert rec is not None
    receipt = svc.erase_scope(
        tenant_id="tenant-a",
        scope=MemoryScope(kind=MemoryScopeKind.ASSIGNMENT, id="as-1"),
        actor="op",
        layer=MemoryLayer.SHORT,
    )
    assert receipt.count == 1
    assert is_memory_error(MemoryPolicyDeniedError("x"))

    def boom_insert(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("db locked")

    svc._store.insert_memory = boom_insert  # type: ignore[method-assign]  # noqa: SLF001
    assert (
        svc.write(
            tenant_id="tenant-a",
            layer=MemoryLayer.SHORT,
            scope=MemoryScope(kind=MemoryScopeKind.TICKET, id="tk-9"),
            content="will degrade",
            origin="worker_summary",
        )
        is None
    )
    assert any(e["op"] == "write" for e in svc.degraded_events)
