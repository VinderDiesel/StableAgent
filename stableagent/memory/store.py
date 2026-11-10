"""MemoryStore：独立 ObservabilityStore 子类，memories 表 + p7-memory-v1。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from stableagent.memory import MEMORY_SCHEMA_VERSION
from stableagent.memory.errors import MemoryNotFoundError, MemoryTenantDeniedError
from stableagent.memory.model import (
    MemoryAuthContext,
    MemoryClassification,
    MemoryLayer,
    MemoryRecord,
    MemoryScope,
    MemoryScopeKind,
)
from stableagent.observability.store import ObservabilityStore
from stableagent.orchestration.model import Clock

_MEMORIES_DDL = (
    "CREATE TABLE IF NOT EXISTS memories ("
    " id TEXT PRIMARY KEY,"
    " tenant_id TEXT NOT NULL,"
    " layer TEXT NOT NULL,"
    " scope_kind TEXT NOT NULL,"
    " scope_id TEXT NOT NULL,"
    " origin TEXT NOT NULL,"
    " content TEXT NOT NULL,"
    " content_digest TEXT NOT NULL,"
    " embedding_ref TEXT,"
    " created_at INTEGER NOT NULL,"
    " updated_at INTEGER NOT NULL,"
    " expires_at INTEGER,"
    " confidence REAL NOT NULL DEFAULT 1.0,"
    " classification TEXT NOT NULL,"
    " deleted_at INTEGER,"
    " provenance TEXT NOT NULL DEFAULT '{}')"
)

_MEMORIES_INDEXES = (
    "CREATE INDEX IF NOT EXISTS memories_tenant_scope"
    " ON memories (tenant_id, layer, scope_kind, scope_id, created_at)",
    "CREATE INDEX IF NOT EXISTS memories_digest"
    " ON memories (tenant_id, scope_kind, scope_id, content_digest)",
    "CREATE UNIQUE INDEX IF NOT EXISTS memories_active_digest"
    " ON memories (tenant_id, layer, scope_kind, scope_id, content_digest)"
    " WHERE deleted_at IS NULL",
)


def _row_to_record(row: sqlite3.Row) -> MemoryRecord:
    return MemoryRecord(
        id=row["id"],
        tenant_id=row["tenant_id"],
        layer=MemoryLayer(row["layer"]),
        scope_kind=MemoryScopeKind(row["scope_kind"]),
        scope_id=row["scope_id"],
        origin=row["origin"],
        content=row["content"],
        content_digest=row["content_digest"],
        embedding_ref=row["embedding_ref"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        expires_at=row["expires_at"],
        confidence=float(row["confidence"]),
        classification=MemoryClassification(row["classification"]),
        deleted_at=row["deleted_at"],
        provenance=json.loads(row["provenance"]),
    )


class MemoryStore(ObservabilityStore):
    """共享 SQLite 路径可选；只加 memories 表与 marker，不改 Supervisor 继承链。"""

    def __init__(self, db_path: str | Path, *, clock: Clock) -> None:
        super().__init__(db_path, clock=clock)
        with self.transaction():
            self._conn.execute(_MEMORIES_DDL)
            for ddl in _MEMORIES_INDEXES:
                self._conn.execute(ddl)
            self._conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (MEMORY_SCHEMA_VERSION, self._clock.now_ms()),
            )

    def insert_memory(self, record: MemoryRecord) -> None:
        self._conn.execute(
            "INSERT INTO memories"
            " (id, tenant_id, layer, scope_kind, scope_id, origin, content, content_digest,"
            "  embedding_ref, created_at, updated_at, expires_at, confidence, classification,"
            "  deleted_at, provenance)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.id,
                record.tenant_id,
                record.layer.value,
                record.scope_kind.value,
                record.scope_id,
                record.origin,
                record.content,
                record.content_digest,
                record.embedding_ref,
                record.created_at,
                record.updated_at,
                record.expires_at,
                record.confidence,
                record.classification.value,
                record.deleted_at,
                json.dumps(record.provenance, ensure_ascii=False, separators=(",", ":")),
            ),
        )

    def get_memory(self, memory_id: str, *, tenant_id: str) -> MemoryRecord:
        row = self._conn.execute(
            "SELECT * FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()
        if row is None:
            raise MemoryNotFoundError(f"memory not found: {memory_id}")
        record = _row_to_record(row)
        if record.tenant_id != tenant_id:
            raise MemoryTenantDeniedError(
                f"tenant {tenant_id} cannot access memory owned by {record.tenant_id}"
            )
        return record

    def find_by_digest(
        self,
        *,
        tenant_id: str,
        layer: MemoryLayer,
        scope: MemoryScope,
        content_digest: str,
    ) -> MemoryRecord | None:
        row = self._conn.execute(
            "SELECT * FROM memories"
            " WHERE tenant_id = ? AND layer = ? AND scope_kind = ? AND scope_id = ?"
            "   AND content_digest = ? AND deleted_at IS NULL"
            " LIMIT 1",
            (tenant_id, layer.value, scope.kind.value, scope.id, content_digest),
        ).fetchone()
        return _row_to_record(row) if row is not None else None

    def count_active_in_scope(
        self,
        *,
        tenant_id: str,
        layer: MemoryLayer,
        scope: MemoryScope,
        now_ms: int,
    ) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM memories"
            " WHERE tenant_id = ? AND layer = ? AND scope_kind = ? AND scope_id = ?"
            "   AND deleted_at IS NULL"
            "   AND (expires_at IS NULL OR expires_at > ?)",
            (tenant_id, layer.value, scope.kind.value, scope.id, now_ms),
        ).fetchone()
        return int(row["n"]) if row is not None else 0

    def list_candidates(
        self,
        auth: MemoryAuthContext,
        *,
        now_ms: int,
        limit: int = 500,
    ) -> list[MemoryRecord]:
        if not auth.allowed_scopes:
            return []
        layers = tuple(layer.value for layer in auth.layers)
        if not layers:
            return []
        scope_clauses: list[str] = []
        params: list[Any] = [auth.tenant_id, *layers, now_ms]
        for scope in auth.allowed_scopes:
            scope_clauses.append("(scope_kind = ? AND scope_id = ?)")
            params.extend([scope.kind.value, scope.id])
        layer_placeholders = ",".join("?" for _ in layers)
        scope_sql = " OR ".join(scope_clauses)
        params.append(limit)
        rows = self._conn.execute(
            f"SELECT * FROM memories"
            f" WHERE tenant_id = ?"
            f"   AND layer IN ({layer_placeholders})"
            f"   AND deleted_at IS NULL"
            f"   AND (expires_at IS NULL OR expires_at > ?)"
            f"   AND ({scope_sql})"
            f" ORDER BY created_at DESC, id DESC"
            f" LIMIT ?",
            params,
        ).fetchall()
        return [_row_to_record(row) for row in rows]

    def soft_delete(
        self,
        memory_ids: list[str],
        *,
        tenant_id: str,
        deleted_at: int,
    ) -> list[str]:
        erased: list[str] = []
        for memory_id in memory_ids:
            row = self._conn.execute(
                "SELECT tenant_id, deleted_at FROM memories WHERE id = ?",
                (memory_id,),
            ).fetchone()
            if row is None:
                continue
            if row["tenant_id"] != tenant_id:
                raise MemoryTenantDeniedError(
                    f"tenant {tenant_id} cannot erase memory {memory_id}"
                )
            if row["deleted_at"] is not None:
                continue
            self._conn.execute(
                "UPDATE memories SET deleted_at = ?, updated_at = ? WHERE id = ?",
                (deleted_at, deleted_at, memory_id),
            )
            erased.append(memory_id)
        return erased

    def soft_delete_by_scope(
        self,
        *,
        tenant_id: str,
        scope: MemoryScope,
        deleted_at: int,
        layer: MemoryLayer | None = None,
    ) -> list[str]:
        clauses = [
            "tenant_id = ?",
            "scope_kind = ?",
            "scope_id = ?",
            "deleted_at IS NULL",
        ]
        params: list[Any] = [tenant_id, scope.kind.value, scope.id]
        if layer is not None:
            clauses.append("layer = ?")
            params.append(layer.value)
        rows = self._conn.execute(
            f"SELECT id FROM memories WHERE {' AND '.join(clauses)}",
            params,
        ).fetchall()
        ids = [str(row["id"]) for row in rows]
        return self.soft_delete(ids, tenant_id=tenant_id, deleted_at=deleted_at)
