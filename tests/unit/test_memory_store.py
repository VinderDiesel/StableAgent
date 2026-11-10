"""P7 M4：MemoryStore schema、跨租户拒绝、软删。"""

from __future__ import annotations

from pathlib import Path

import pytest

from stableagent.memory import MEMORY_SCHEMA_VERSION
from stableagent.memory.errors import MemoryTenantDeniedError
from stableagent.memory.model import (
    MemoryAuthContext,
    MemoryClassification,
    MemoryLayer,
    MemoryRecord,
    MemoryScope,
    MemoryScopeKind,
)
from stableagent.memory.store import MemoryStore
from tests.support import FixedClock


def _record(**kwargs: object) -> MemoryRecord:
    base: dict[str, object] = {
        "id": "mem-1",
        "tenant_id": "tenant-a",
        "layer": MemoryLayer.SHORT,
        "scope_kind": MemoryScopeKind.TICKET,
        "scope_id": "tk-1",
        "origin": "worker_summary",
        "content": "hello memory",
        "content_digest": "d" * 64,
        "created_at": 1_000,
        "updated_at": 1_000,
        "expires_at": 10_000,
        "classification": MemoryClassification.INTERNAL,
    }
    base.update(kwargs)
    return MemoryRecord.model_validate(base)


def test_schema_marker_and_open_empty_db(tmp_path: Path) -> None:
    clock = FixedClock(1_000)
    store = MemoryStore(tmp_path / "m.db", clock=clock)
    versions = [
        row[0]
        for row in store._conn.execute(  # noqa: SLF001
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
    ]
    assert MEMORY_SCHEMA_VERSION in versions
    assert "p7-observability-v1" in versions


def test_cross_tenant_get_denied(tmp_path: Path) -> None:
    clock = FixedClock(1_000)
    store = MemoryStore(tmp_path / "m.db", clock=clock)
    with store.transaction():
        store.insert_memory(_record())
    with pytest.raises(MemoryTenantDeniedError):
        store.get_memory("mem-1", tenant_id="tenant-b")
    assert store.get_memory("mem-1", tenant_id="tenant-a").content == "hello memory"


def test_list_candidates_respects_tenant_and_scope(tmp_path: Path) -> None:
    clock = FixedClock(1_000)
    store = MemoryStore(tmp_path / "m.db", clock=clock)
    with store.transaction():
        store.insert_memory(_record())
        store.insert_memory(
            _record(
                id="mem-2",
                tenant_id="tenant-b",
                content_digest="e" * 64,
                content="other tenant",
            )
        )
        store.insert_memory(
            _record(
                id="mem-3",
                scope_id="tk-2",
                content_digest="f" * 64,
                content="other ticket",
            )
        )
    auth = MemoryAuthContext(
        tenant_id="tenant-a",
        allowed_scopes=frozenset(
            {MemoryScope(kind=MemoryScopeKind.TICKET, id="tk-1")}
        ),
    )
    rows = store.list_candidates(auth, now_ms=2_000)
    assert [r.id for r in rows] == ["mem-1"]


def test_soft_delete_hides_from_candidates(tmp_path: Path) -> None:
    clock = FixedClock(1_000)
    store = MemoryStore(tmp_path / "m.db", clock=clock)
    with store.transaction():
        store.insert_memory(_record())
        erased = store.soft_delete(["mem-1"], tenant_id="tenant-a", deleted_at=2_000)
    assert erased == ["mem-1"]
    auth = MemoryAuthContext(
        tenant_id="tenant-a",
        allowed_scopes=frozenset(
            {MemoryScope(kind=MemoryScopeKind.TICKET, id="tk-1")}
        ),
    )
    assert store.list_candidates(auth, now_ms=3_000) == []


def test_expired_not_listed(tmp_path: Path) -> None:
    clock = FixedClock(1_000)
    store = MemoryStore(tmp_path / "m.db", clock=clock)
    with store.transaction():
        store.insert_memory(_record(expires_at=1_500))
    auth = MemoryAuthContext(
        tenant_id="tenant-a",
        allowed_scopes=frozenset(
            {MemoryScope(kind=MemoryScopeKind.TICKET, id="tk-1")}
        ),
    )
    assert store.list_candidates(auth, now_ms=2_000) == []


def test_get_missing_and_empty_auth_layers(tmp_path: Path) -> None:
    from stableagent.memory.errors import MemoryNotFoundError

    clock = FixedClock(1_000)
    store = MemoryStore(tmp_path / "m.db", clock=clock)
    with pytest.raises(MemoryNotFoundError):
        store.get_memory("missing", tenant_id="tenant-a")
    assert (
        store.list_candidates(
            MemoryAuthContext(tenant_id="tenant-a", allowed_scopes=frozenset()),
            now_ms=1_000,
        )
        == []
    )
    assert (
        store.list_candidates(
            MemoryAuthContext(
                tenant_id="tenant-a",
                allowed_scopes=frozenset(
                    {MemoryScope(kind=MemoryScopeKind.TICKET, id="tk-1")}
                ),
                layers=frozenset(),
            ),
            now_ms=1_000,
        )
        == []
    )


def test_soft_delete_by_scope_and_tenant_erase_denied(tmp_path: Path) -> None:
    clock = FixedClock(1_000)
    store = MemoryStore(tmp_path / "m.db", clock=clock)
    scope = MemoryScope(kind=MemoryScopeKind.TICKET, id="tk-1")
    with store.transaction():
        store.insert_memory(_record())
        store.insert_memory(
            _record(id="mem-2", content_digest="e" * 64, content="second")
        )
        erased = store.soft_delete_by_scope(
            tenant_id="tenant-a",
            scope=scope,
            deleted_at=2_000,
            layer=MemoryLayer.SHORT,
        )
    assert set(erased) == {"mem-1", "mem-2"}
    with store.transaction():
        store.insert_memory(_record(id="mem-3", content_digest="f" * 64))
        with pytest.raises(MemoryTenantDeniedError):
            store.soft_delete(["mem-3"], tenant_id="tenant-b", deleted_at=3_000)
        assert store.soft_delete(["nope"], tenant_id="tenant-a", deleted_at=3_000) == []
        # 已删再删
        assert store.soft_delete(["mem-1"], tenant_id="tenant-a", deleted_at=3_000) == []
