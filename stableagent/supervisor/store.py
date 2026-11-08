"""Supervisor 持久化。表、索引与 CRUD 全部在本包，orchestration 不知道 supervisor。

与 P4 Store 共用连接与 BEGIN IMMEDIATE，因此 Trigger/Decision/Subscription 和
Ticket/Assignment/Effect 可以在同一事务内落库（规格 2.1、ADR-06 决策 2/7）。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from stableagent.orchestration.model import Clock
from stableagent.orchestration.store import Store
from stableagent.supervisor import SUPERVISOR_SCHEMA_VERSION
from stableagent.supervisor.model import (
    Thread,
    ThreadKind,
    ThreadStatus,
    Trigger,
    TriggerKind,
)

_SUPERVISOR_SCHEMA: dict[str, str] = {
    "triggers": (
        "CREATE TABLE IF NOT EXISTS triggers ("
        " id TEXT PRIMARY KEY, source TEXT NOT NULL, external_id TEXT NOT NULL,"
        " kind TEXT NOT NULL, actor TEXT NOT NULL,"
        " thread_keys TEXT NOT NULL DEFAULT '[]',"
        " occurred_at INTEGER NOT NULL, received_at INTEGER NOT NULL,"
        " payload TEXT NOT NULL, digest TEXT NOT NULL,"
        " UNIQUE (source, external_id))"
    ),
    "threads": (
        "CREATE TABLE IF NOT EXISTS threads ("
        " key TEXT PRIMARY KEY, source TEXT NOT NULL, repository TEXT NOT NULL,"
        " kind TEXT NOT NULL, external_ref TEXT NOT NULL,"
        " last_trigger_at INTEGER NOT NULL, status TEXT NOT NULL,"
        " UNIQUE (source, repository, kind, external_ref))"
    ),
}

_SUPERVISOR_INDEXES: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS triggers_received ON triggers (received_at, id)",
    "CREATE INDEX IF NOT EXISTS threads_last_trigger ON threads (last_trigger_at, key)",
)


def _row_to_trigger(row: sqlite3.Row) -> Trigger:
    return Trigger(
        id=row["id"],
        source=row["source"],
        external_id=row["external_id"],
        kind=TriggerKind(row["kind"]),
        actor=row["actor"],
        thread_keys=json.loads(row["thread_keys"]),
        occurred_at=row["occurred_at"],
        received_at=row["received_at"],
        payload=json.loads(row["payload"]),
        digest=row["digest"],
    )


def _row_to_thread(row: sqlite3.Row) -> Thread:
    return Thread(
        key=row["key"],
        source=row["source"],
        repository=row["repository"],
        kind=ThreadKind(row["kind"]),
        external_ref=row["external_ref"],
        last_trigger_at=row["last_trigger_at"],
        status=ThreadStatus(row["status"]),
    )


class SupervisorStore(Store):
    """P4 Store 的增量扩展：同一连接、同一事务、只加表。"""

    def __init__(self, db_path: str | Path, *, clock: Clock) -> None:
        super().__init__(db_path, clock=clock)
        with self.transaction():
            for ddl in _SUPERVISOR_SCHEMA.values():
                self._conn.execute(ddl)
            for ddl in _SUPERVISOR_INDEXES:
                self._conn.execute(ddl)
            self._conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (SUPERVISOR_SCHEMA_VERSION, self._clock.now_ms()),
            )

    def insert_trigger(self, trigger: Trigger) -> bool:
        cursor = self._conn.execute(
            "INSERT OR IGNORE INTO triggers"
            " (id, source, external_id, kind, actor, thread_keys,"
            " occurred_at, received_at, payload, digest)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                trigger.id,
                trigger.source,
                trigger.external_id,
                trigger.kind.value,
                trigger.actor,
                json.dumps(trigger.thread_keys, sort_keys=True, ensure_ascii=False),
                trigger.occurred_at,
                trigger.received_at,
                json.dumps(trigger.payload, sort_keys=True, ensure_ascii=False),
                trigger.digest,
            ),
        )
        return int(cursor.rowcount) == 1

    def get_trigger(self, trigger_id: str) -> Trigger | None:
        row = self._conn.execute("SELECT * FROM triggers WHERE id = ?", (trigger_id,)).fetchone()
        return _row_to_trigger(row) if row else None

    def get_trigger_by_external(self, source: str, external_id: str) -> Trigger | None:
        row = self._conn.execute(
            "SELECT * FROM triggers WHERE source = ? AND external_id = ?",
            (source, external_id),
        ).fetchone()
        return _row_to_trigger(row) if row else None

    def list_triggers(self, *, thread_key: str | None = None, limit: int = 100) -> list[Trigger]:
        if thread_key is None:
            rows = self._conn.execute(
                "SELECT * FROM triggers ORDER BY received_at ASC, id ASC LIMIT ?", (limit,)
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT t.* FROM triggers t"
                " WHERE EXISTS (SELECT 1 FROM json_each(t.thread_keys) WHERE value = ?)"
                " ORDER BY t.received_at ASC, t.id ASC LIMIT ?",
                (thread_key, limit),
            ).fetchall()
        return [_row_to_trigger(row) for row in rows]

    def upsert_thread(self, thread: Thread) -> None:
        self._conn.execute(
            "INSERT INTO threads (key, source, repository, kind, external_ref,"
            " last_trigger_at, status) VALUES (?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET"
            " last_trigger_at = MAX(threads.last_trigger_at, excluded.last_trigger_at),"
            " status = CASE"
            "   WHEN threads.status = 'stopped' THEN 'stopped'"
            "   WHEN excluded.status = 'stopped' THEN 'stopped'"
            "   WHEN threads.status = 'closed' AND excluded.status = 'open' THEN 'closed'"
            "   ELSE excluded.status END",
            (
                thread.key,
                thread.source,
                thread.repository,
                thread.kind.value,
                thread.external_ref,
                thread.last_trigger_at,
                thread.status.value,
            ),
        )

    def get_thread(self, key: str) -> Thread | None:
        row = self._conn.execute("SELECT * FROM threads WHERE key = ?", (key,)).fetchone()
        return _row_to_thread(row) if row else None

    def list_threads(self, *, limit: int = 100) -> list[Thread]:
        rows = self._conn.execute(
            "SELECT * FROM threads ORDER BY last_trigger_at DESC, key ASC LIMIT ?", (limit,)
        ).fetchall()
        return [_row_to_thread(row) for row in rows]
