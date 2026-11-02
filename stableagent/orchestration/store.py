"""编排层持久化。本模块只做持久化与约束，不做调度或策略判断（B7）。

事务模型：sqlite3 以 isolation_level=None 运行，事务由 transaction() 显式 BEGIN IMMEDIATE /
COMMIT / ROLLBACK 管理。事务体内禁止 await——驱动循环是单线程，同步段对协程不可分割，
因此"状态与 effect 同事务"是可证明性质而非约定（ADR-04 决策 3）。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from stableagent.orchestration import ORCHESTRATION_SCHEMA_VERSION
from stableagent.orchestration.errors import ReentrantTransactionError
from stableagent.orchestration.model import Clock

_SCHEMA: dict[str, str] = {
    "schema_migrations": (
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version TEXT PRIMARY KEY, applied_at INTEGER NOT NULL)"
    ),
    "mandates": (
        "CREATE TABLE IF NOT EXISTS mandates ("
        " id TEXT PRIMARY KEY, origin_kind TEXT NOT NULL, requester TEXT NOT NULL,"
        " scope TEXT NOT NULL DEFAULT '',"
        " policy_version TEXT NOT NULL, granted_at INTEGER NOT NULL)"
    ),
    "tickets": (
        "CREATE TABLE IF NOT EXISTS tickets ("
        " id TEXT PRIMARY KEY, title TEXT NOT NULL, task TEXT NOT NULL,"
        " created_at INTEGER NOT NULL, status TEXT NOT NULL, mandate_id TEXT NOT NULL,"
        " parent_id TEXT, ended_at INTEGER, queued_at INTEGER, woken_at INTEGER)"
    ),
    "assignments": (
        "CREATE TABLE IF NOT EXISTS assignments ("
        " id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL, attempt INTEGER NOT NULL,"
        " status TEXT NOT NULL, worker_run_id TEXT, started_at INTEGER, ended_at INTEGER,"
        " checkpoint_run_id TEXT, failure TEXT, worker_config_version TEXT,"
        " worker_config TEXT NOT NULL DEFAULT '{}',"
        " UNIQUE (ticket_id, attempt), UNIQUE (worker_run_id))"
    ),
    "inputs": (
        "CREATE TABLE IF NOT EXISTS inputs ("
        " id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL, sequence INTEGER NOT NULL,"
        " kind TEXT NOT NULL, payload TEXT NOT NULL, created_at INTEGER NOT NULL,"
        " delivered_at INTEGER, consumed_at INTEGER, UNIQUE (ticket_id, sequence))"
    ),
    "reports": (
        "CREATE TABLE IF NOT EXISTS reports ("
        " id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL, assignment_id TEXT NOT NULL,"
        " kind TEXT NOT NULL, summary TEXT NOT NULL, created_at INTEGER NOT NULL,"
        " event_step INTEGER, details TEXT NOT NULL DEFAULT '{}')"
    ),
    "dependencies": (
        "CREATE TABLE IF NOT EXISTS dependencies ("
        " ticket_id TEXT NOT NULL, depends_on_ticket_id TEXT NOT NULL,"
        " on_failure TEXT NOT NULL DEFAULT 'block',"
        " PRIMARY KEY (ticket_id, depends_on_ticket_id))"
    ),
    "effects": (
        "CREATE TABLE IF NOT EXISTS effects ("
        " id TEXT PRIMARY KEY, kind TEXT NOT NULL, idempotency_key TEXT NOT NULL,"
        " payload TEXT NOT NULL, state TEXT NOT NULL, attempts INTEGER NOT NULL,"
        " due_at INTEGER NOT NULL, last_error TEXT,"
        " UNIQUE (kind, idempotency_key))"
    ),
}
ORCHESTRATION_TABLES = tuple(_SCHEMA)


class Store:
    def __init__(self, db_path: str | Path, *, clock: Clock) -> None:
        self._clock = clock
        self._conn = sqlite3.connect(str(db_path), isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._in_tx = False
        self._create_schema()

    def _create_schema(self) -> None:
        with self.transaction():
            for ddl in _SCHEMA.values():
                self._conn.execute(ddl)
            self._conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (ORCHESTRATION_SCHEMA_VERSION, self._clock.now_ms()),
            )

    @property
    def in_transaction(self) -> bool:
        return self._in_tx

    @contextmanager
    def transaction(self) -> Iterator[None]:
        if self._in_tx:
            raise ReentrantTransactionError()
        self._in_tx = True
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            yield
            self._conn.execute("COMMIT")
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        finally:
            self._in_tx = False

    def schema_version(self) -> str:
        row = self._conn.execute(
            "SELECT version FROM schema_migrations ORDER BY applied_at DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else "0"

    def close(self) -> None:
        self._conn.close()
