"""A1（旧库升级半边）+ L3（迁移半边）：`p5-control-v1` 兼容加列、两新表、幂等且在事务内。"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from stableagent.orchestration.store import Store
from tests.support import FixedClock

P5_CONTROL_VERSION = "p5-control-v1"

# P4 收口提交 234f620 的 8 张表原始 DDL（逐字抄录）。"旧库"必须由本文件自己建出来：
# 若改用当前 `_SCHEMA`，新列与新表已经在里面，升级路径就无从证明。
_P4_DDL: tuple[str, ...] = (
    "CREATE TABLE IF NOT EXISTS schema_migrations ("
    " version TEXT PRIMARY KEY, applied_at INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS mandates ("
    " id TEXT PRIMARY KEY, origin_kind TEXT NOT NULL, requester TEXT NOT NULL,"
    " scope TEXT NOT NULL DEFAULT '',"
    " policy_version TEXT NOT NULL, granted_at INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS tickets ("
    " id TEXT PRIMARY KEY, title TEXT NOT NULL, task TEXT NOT NULL,"
    " created_at INTEGER NOT NULL, status TEXT NOT NULL, mandate_id TEXT NOT NULL,"
    " parent_id TEXT, ended_at INTEGER, queued_at INTEGER, woken_at INTEGER)",
    "CREATE TABLE IF NOT EXISTS assignments ("
    " id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL, attempt INTEGER NOT NULL,"
    " status TEXT NOT NULL, worker_run_id TEXT, started_at INTEGER, ended_at INTEGER,"
    " checkpoint_run_id TEXT, failure TEXT, deadline_at INTEGER,"
    " worker_config_version TEXT,"
    " worker_config TEXT NOT NULL DEFAULT '{}',"
    " UNIQUE (ticket_id, attempt), UNIQUE (worker_run_id))",
    "CREATE TABLE IF NOT EXISTS inputs ("
    " id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL, sequence INTEGER NOT NULL,"
    " kind TEXT NOT NULL, payload TEXT NOT NULL, created_at INTEGER NOT NULL,"
    " delivered_at INTEGER, consumed_at INTEGER, state TEXT NOT NULL DEFAULT 'pending',"
    " UNIQUE (ticket_id, sequence))",
    "CREATE TABLE IF NOT EXISTS reports ("
    " id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL, assignment_id TEXT NOT NULL,"
    " kind TEXT NOT NULL, summary TEXT NOT NULL, created_at INTEGER NOT NULL,"
    " event_step INTEGER, details TEXT NOT NULL DEFAULT '{}')",
    "CREATE TABLE IF NOT EXISTS dependencies ("
    " ticket_id TEXT NOT NULL, depends_on_ticket_id TEXT NOT NULL,"
    " on_failure TEXT NOT NULL DEFAULT 'block',"
    " PRIMARY KEY (ticket_id, depends_on_ticket_id))",
    "CREATE TABLE IF NOT EXISTS effects ("
    " id TEXT PRIMARY KEY, kind TEXT NOT NULL, idempotency_key TEXT NOT NULL,"
    " payload TEXT NOT NULL, state TEXT NOT NULL, attempts INTEGER NOT NULL,"
    " due_at INTEGER NOT NULL, last_error TEXT,"
    " UNIQUE (kind, idempotency_key))",
)

MANDATE_P4_COLUMNS = ("id", "origin_kind", "requester", "scope", "policy_version", "granted_at")
MANDATE_P5_COLUMNS = (
    "repository",
    "base_ref",
    "actions",
    "allowed_paths",
    "allowed_commands",
    "allowed_mcp",
    "network_profile",
    "expires_at",
    "requires_approval",
    "request_origin",
    "request_digest",
)
LEGACY_MANDATE = {
    "id": "md-legacy",
    "origin_kind": "local",
    "requester": "local",
    "scope": "",
    "policy_version": "p4-local-v1",
    "granted_at": 1_000,
}


def _build_legacy_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.row_factory = sqlite3.Row
        for ddl in _P4_DDL:
            conn.execute(ddl)
        columns = ", ".join(MANDATE_P4_COLUMNS)
        placeholders = ", ".join(f":{name}" for name in MANDATE_P4_COLUMNS)
        conn.execute(f"INSERT INTO mandates ({columns}) VALUES ({placeholders})", LEGACY_MANDATE)
        conn.execute("INSERT INTO schema_migrations (version, applied_at) VALUES ('0.1.0', 1000)")
        conn.commit()
    finally:
        conn.close()


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {row[0] for row in rows}


def _mandate_columns(conn: sqlite3.Connection) -> set[str]:
    return {row["name"] for row in conn.execute("PRAGMA table_info(mandates)")}


def test_legacy_p4_database_upgrades_without_touching_rows(tmp_path: Path) -> None:
    """A1：旧库开得出、加得了列、两新表建得起，且旧行的 6 个原字段与 policy_version 逐字不变。"""
    db_path = tmp_path / "p4.db"
    _build_legacy_db(db_path)

    before = sqlite3.connect(str(db_path))
    before.row_factory = sqlite3.Row
    try:
        assert _mandate_columns(before) == set(MANDATE_P4_COLUMNS)
        assert not {"approval_requests", "authorization_audit"} & _table_names(before)
    finally:
        before.close()

    handle = Store(db_path, clock=FixedClock())
    try:
        conn = handle._conn
        assert set(MANDATE_P5_COLUMNS) <= _mandate_columns(conn)
        assert {"approval_requests", "authorization_audit"} <= _table_names(conn)
        row = conn.execute("SELECT * FROM mandates WHERE id='md-legacy'").fetchone()
        assert {name: row[name] for name in MANDATE_P4_COLUMNS} == LEGACY_MANDATE  # 6 字段逐等
        assert row["policy_version"] == "p4-local-v1"  # 不回写、不重写语义
        for column in MANDATE_P5_COLUMNS:
            assert row[column] in (None, "[]", 0), (column, row[column])
        assert handle.schema_version() == P5_CONTROL_VERSION
    finally:
        handle.close()


def test_migration_marker_is_idempotent(tmp_path: Path) -> None:
    """重复打开同一库，`schema_migrations` 里 p5-control-v1 恰好 1 行，列也不重复添加。"""
    db_path = tmp_path / "orchestration.db"
    for _ in range(3):
        handle = Store(db_path, clock=FixedClock())
        try:
            markers = handle._conn.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE version=?", (P5_CONTROL_VERSION,)
            ).fetchone()[0]
            assert markers == 1
            assert len(_mandate_columns(handle._conn)) == len(set(_mandate_columns(handle._conn)))
        finally:
            handle.close()


def test_new_tables_have_the_authorization_columns(tmp_path: Path) -> None:
    """两新表的列与 ADR-05 决策 3 的写法一一对应（Task 6/7 的仓储方法依赖这套列名）。"""
    handle = Store(tmp_path / "orchestration.db", clock=FixedClock())
    try:
        assert _mandate_columns(handle._conn) == set(MANDATE_P4_COLUMNS) | set(MANDATE_P5_COLUMNS)
        approval_columns = {
            row["name"] for row in handle._conn.execute("PRAGMA table_info(approval_requests)")
        }
        audit_columns = {
            row["name"] for row in handle._conn.execute("PRAGMA table_info(authorization_audit)")
        }
    finally:
        handle.close()
    assert approval_columns == {
        "id",
        "ticket_id",
        "assignment_id",
        "action",
        "resource_digest",
        "reason",
        "requested_at",
        "status",
        "decided_by",
        "decided_at",
    }
    assert audit_columns == {
        "id",
        "kind",
        "ticket_id",
        "assignment_id",
        "mandate_id",
        "action",
        "resource_digest",
        "policy_version",
        "decision",
        "reason",
        "created_at",
    }


def test_migration_runs_inside_transaction(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """L3（迁移半边）：加列与 marker 都在 `Store.transaction()` 体内，`_migrate` 只开一次事务。"""
    entries: list[int] = []
    original_transaction = Store.transaction
    original_migrate = Store._migrate

    def counting_transaction(self: Store) -> Any:
        entries.append(1)
        return original_transaction(self)

    def spying_migrate(self: Store) -> None:
        before = len(entries)
        original_migrate(self)
        assert len(entries) - before == 1
        assert self.in_transaction is False  # 事务体已收尾，不留悬挂事务

    monkeypatch.setattr(Store, "transaction", counting_transaction)
    monkeypatch.setattr(Store, "_migrate", spying_migrate)
    handle = Store(tmp_path / "orchestration.db", clock=FixedClock())
    handle.close()
    assert len(entries) == 2  # _create_schema 一次 + _migrate 一次
