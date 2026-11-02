"""B1/B2/B3/B4/B6 + L6：schema、事务原子性、重入守卫、唯一键、重开无损。"""

from __future__ import annotations

from pathlib import Path

import pytest

from stableagent.orchestration.errors import ReentrantTransactionError
from stableagent.orchestration.store import ORCHESTRATION_TABLES, Store
from tests.support import FixedClock

TABLES = {
    "tickets",
    "mandates",
    "assignments",
    "inputs",
    "reports",
    "dependencies",
    "effects",
    "schema_migrations",
}

# 本文件允许直接用 store._conn 写裸 SQL：Task 4 的验收对象就是 schema 与事务，仓储方法要到
# Task 5 才存在。这种白盒写法只限 test_orchestration_store.py，其它测试一律走公开仓储方法。


@pytest.fixture
def store(tmp_path: Path):
    handle = Store(tmp_path / "orchestration.db", clock=FixedClock())
    yield handle
    handle.close()


def test_all_eight_tables_exist_with_schema_version(store: Store) -> None:
    names = {
        row[0]
        for row in store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert names >= TABLES
    assert store.schema_version() == "0.1.0"


def test_transaction_is_atomic_for_both_ticket_and_effect(store: Store) -> None:
    class Boom(RuntimeError):
        pass

    with pytest.raises(Boom), store.transaction():
        store._conn.execute(
            "INSERT INTO tickets (id,title,task,created_at,status,mandate_id) "
            "VALUES ('tk-1','t','x',1,'open','md-1')"
        )
        store._conn.execute(
            "INSERT INTO effects (id,kind,idempotency_key,payload,state,attempts,due_at) "
            "VALUES ('ef-1','launch_assignment','k','{}','pending',0,1)"
        )
        raise Boom()
    assert store._conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 0
    assert store._conn.execute("SELECT COUNT(*) FROM effects").fetchone()[0] == 0


def test_nested_transaction_is_rejected_and_flag_clears(store: Store) -> None:
    assert store.in_transaction is False
    with store.transaction():
        assert store.in_transaction is True
        with pytest.raises(ReentrantTransactionError), store.transaction():
            pass
    assert store.in_transaction is False


def test_reopen_loses_nothing_and_unique_keys_hold(store: Store, tmp_path: Path) -> None:
    with store.transaction():
        store._conn.execute(
            "INSERT INTO tickets (id,title,task,created_at,status,mandate_id) "
            "VALUES ('tk-1','t','x',1,'open','md-1')"
        )
    store.close()
    reopened = Store(tmp_path / "orchestration.db", clock=FixedClock())
    try:
        assert reopened._conn.execute("SELECT id FROM tickets").fetchone()[0] == "tk-1"
        with pytest.raises(Exception, match="UNIQUE"), reopened.transaction():
            reopened._conn.execute(
                "INSERT INTO tickets (id,title,task,created_at,status,mandate_id) "
                "VALUES ('tk-1','t','x',1,'open','md-2')"
            )
    finally:
        reopened.close()


def test_ddl_is_exposed_for_documentation() -> None:
    assert set(ORCHESTRATION_TABLES) == TABLES
