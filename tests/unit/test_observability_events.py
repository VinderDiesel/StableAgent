"""P7 M1：DomainEvent 契约与 ObservabilityStore / Emitter。"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from stableagent.observability import DEFAULT_TENANT, OBSERVABILITY_SCHEMA_VERSION
from stableagent.observability.emitter import DomainEventEmitter
from stableagent.observability.events import DomainEvent, DomainEventKind
from stableagent.observability.store import ObservabilityStore
from stableagent.supervisor.store import SupervisorStore


class _Clock:
    def __init__(self) -> None:
        self._now = 1_000

    def now_ms(self) -> int:
        self._now += 1
        return self._now


class _Ids:
    def __init__(self) -> None:
        self._n = 0

    def new_id(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}-{self._n}"


def test_domain_event_requires_tenant() -> None:
    with pytest.raises(ValidationError):
        DomainEvent(
            id="ev-1",
            timestamp=1,
            tenant_id="",
            kind=DomainEventKind.TRIGGER_RECEIVED,
            outcome="ok",
            payload_digest="abc",
        )


def test_domain_event_requires_outcome() -> None:
    with pytest.raises(ValidationError):
        DomainEvent(
            id="ev-1",
            timestamp=1,
            tenant_id=DEFAULT_TENANT,
            kind=DomainEventKind.TRIGGER_RECEIVED,
            outcome="",
            payload_digest="abc",
        )


def test_observability_store_creates_marker_and_table(tmp_path: Path) -> None:
    clock = _Clock()
    store = ObservabilityStore(tmp_path / "obs.db", clock=clock)
    rows = store._conn.execute(
        "SELECT version FROM schema_migrations WHERE version = ?",
        (OBSERVABILITY_SCHEMA_VERSION,),
    ).fetchall()
    assert len(rows) == 1
    tables = {
        r[0]
        for r in store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "domain_events" in tables
    assert "tickets" in tables  # 父 Store 表仍在


def test_supervisor_store_inherits_observability_without_loss(tmp_path: Path) -> None:
    clock = _Clock()
    path = tmp_path / "sup.db"
    first = SupervisorStore(path, clock=clock)
    first._conn.execute(
        "SELECT 1 FROM schema_migrations WHERE version = ?",
        (OBSERVABILITY_SCHEMA_VERSION,),
    ).fetchone()
    # 再打开幂等
    second = SupervisorStore(path, clock=clock)
    assert second is not None


def test_emitter_writes_redacted_event_and_metrics(tmp_path: Path) -> None:
    clock = _Clock()
    ids = _Ids()
    store = ObservabilityStore(tmp_path / "e.db", clock=clock)
    emitter = DomainEventEmitter(store, clock=clock, ids=ids)
    event = emitter.emit(
        DomainEventKind.TRIGGER_RECEIVED,
        outcome="ok",
        trigger_id="tg-1",
        ticket_id="tk-1",
        attrs={"api_key": "secret", "count": 2},
    )
    assert event.tenant_id == DEFAULT_TENANT
    assert event.attrs["api_key"] == "[REDACTED]"
    assert event.attrs["count"] == 2
    listed = store.list_domain_events(ticket_id="tk-1")
    assert len(listed) == 1
    assert listed[0].id == event.id
    assert emitter.metrics.get(
        "domain_events", labels={"kind": "trigger.received", "outcome": "ok"}
    ) == 1
    trace = emitter.traces.get("tk-1")
    assert trace is not None
    assert event.id in trace.children


def test_list_filters_and_audit_helpers(tmp_path: Path) -> None:
    from stableagent.observability.audit import list_kind_events, list_ticket_events

    clock = _Clock()
    ids = _Ids()
    store = ObservabilityStore(tmp_path / "f.db", clock=clock)
    emitter = DomainEventEmitter(store, clock=clock, ids=ids)
    emitter.emit(DomainEventKind.TRIGGER_RECEIVED, outcome="ok", ticket_id="tk-a")
    emitter.emit(DomainEventKind.EFFECT_FAILED, outcome="error", ticket_id="tk-b")
    assert len(list_ticket_events(store, "tk-a")) == 1
    assert len(list_kind_events(store, DomainEventKind.EFFECT_FAILED)) == 1
    assert len(store.list_domain_events(tenant_id=DEFAULT_TENANT, limit=10)) == 2
    snap = emitter.metrics.snapshot()
    assert any(k.startswith("domain_events|") for k in snap)


def test_emit_safe_swallows_sink_errors(tmp_path: Path) -> None:
    clock = _Clock()
    ids = _Ids()
    store = ObservabilityStore(tmp_path / "fail.db", clock=clock)

    def _boom(_event: object) -> None:
        raise RuntimeError("disk full")

    store.insert_domain_event = _boom  # type: ignore[method-assign]
    emitter = DomainEventEmitter(store, clock=clock, ids=ids)
    assert (
        emitter.emit_safe(DomainEventKind.EFFECT_FAILED, outcome="error", effect_id="ef-1")
        is None
    )
    assert emitter.metrics.get("emit_failures") == 1
