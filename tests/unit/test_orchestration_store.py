"""B1/B2/B3/B4/B6 + L6：schema、事务原子性、重入守卫、唯一键、重开无损。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from stableagent.orchestration.errors import ReentrantTransactionError
from stableagent.orchestration.model import (
    Assignment,
    AssignmentStatus,
    Dependency,
    EffectState,
    Mandate,
    Report,
    ReportKind,
    Ticket,
    TicketInput,
    TicketStatus,
)
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


def test_p4_database_keeps_p2_checkpoint_tables_out(store: Store) -> None:
    """L5：编排库只含自己的 8 张表——P2 的 `checkpoints` 不被带进来，两边各自演进。"""
    names = {
        row[0]
        for row in store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    user_tables = {name for name in names if not name.startswith("sqlite_")}
    assert user_tables == TABLES
    assert "checkpoints" not in user_tables


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


def _ticket(ticket_id: str, **kw) -> Ticket:
    base = dict(id=ticket_id, title="t", task="x", created_at=1, mandate_id=f"md-{ticket_id}")
    return Ticket(**{**base, **kw})


def _mandate(ticket_id: str) -> Mandate:
    return Mandate(id=f"md-{ticket_id}", granted_at=1)


def test_ticket_and_assignment_round_trip_through_sqlite(store: Store) -> None:
    ticket = _ticket("tk-1", status=TicketStatus.QUEUED, queued_at=5)
    store.insert_ticket(ticket)
    assert store.get_ticket("tk-1") == ticket
    assignment = Assignment(
        id="as-1",
        ticket_id="tk-1",
        attempt=1,
        status=AssignmentStatus.PENDING,
        worker_config={"model": "gpt-4o"},
    )
    store.insert_assignment(assignment)
    assert store.get_assignment("as-1") == assignment
    assert store.list_assignments("tk-1") == [assignment]
    with pytest.raises(Exception, match="UNIQUE"):
        store.insert_assignment(assignment.model_copy(update={"id": "as-2"}))


def test_worker_run_id_is_unique_across_assignments(store: Store) -> None:
    store.insert_ticket(_ticket("tk-1"))
    store.insert_ticket(_ticket("tk-2"))
    store.insert_assignment(Assignment(id="as-1", ticket_id="tk-1", worker_run_id="r1"))
    with pytest.raises(Exception, match="UNIQUE"):
        store.insert_assignment(Assignment(id="as-2", ticket_id="tk-2", worker_run_id="r1"))


def test_reports_inputs_and_dependencies_round_trip(store: Store) -> None:
    store.insert_ticket(_ticket("tk-1"))
    report = Report(
        id="rp-1",
        ticket_id="tk-1",
        assignment_id="as-1",
        kind=ReportKind.PROGRESS,
        summary="第 2 步",
        created_at=2,
        event_step=2,
        details={"tool": "echo"},
    )
    store.insert_report(report)
    assert store.list_reports("tk-1") == [report]
    ticket_input = TicketInput(
        id="in-1", ticket_id="tk-1", sequence=1, payload="追加线索", created_at=2
    )
    store.insert_input(ticket_input)
    assert store.list_inputs("tk-1", pending_only=True) == [ticket_input]


def test_runnable_order_is_woken_then_queued_then_created(store: Store) -> None:
    store.insert_ticket(_ticket("tk-old", status=TicketStatus.QUEUED, queued_at=100, created_at=1))
    store.insert_ticket(_ticket("tk-new", status=TicketStatus.QUEUED, queued_at=200, created_at=2))
    store.insert_ticket(
        _ticket(
            "tk-woken",
            status=TicketStatus.QUEUED,
            queued_at=300,
            created_at=3,
            woken_at=900,
        )
    )
    assert [t.id for t in store.list_runnable_tickets(1000)] == ["tk-woken", "tk-old", "tk-new"]


def test_pagination_is_stable(store: Store) -> None:
    for index in range(5):
        store.insert_ticket(_ticket(f"tk-{index}", status=TicketStatus.QUEUED, queued_at=index))
    page = store.list_tickets(status=TicketStatus.QUEUED, limit=2, offset=1)
    assert [t.id for t in page] == ["tk-1", "tk-2"]


def test_mandate_is_its_own_row_and_dependencies_round_trip(store: Store) -> None:
    store.insert_mandate(_mandate("tk-1"))
    assert store.get_mandate("md-tk-1") == _mandate("tk-1")
    store.insert_mandate(_mandate("tk-1"))  # INSERT OR IGNORE：重复写不报错也不改行
    store.insert_ticket(_ticket("tk-1"))
    store.insert_ticket(_ticket("tk-2"))
    dep = Dependency(ticket_id="tk-2", depends_on_ticket_id="tk-1", on_failure="continue")
    store.insert_dependency(dep)
    assert store.list_dependencies("tk-2") == [dep]
    assert store.list_dependencies("tk-1") == []


def _insert_effect(store: Store, effect_id: str, key: str, *, due: int = 1) -> None:
    with store.transaction():
        store._conn.execute(
            "INSERT INTO effects (id,kind,idempotency_key,payload,state,attempts,due_at)"
            " VALUES (?, 'launch_assignment', ?, '{}', 'pending', 0, ?)",
            (effect_id, key, due),
        )


def test_effect_kind_idempotency_key_is_unique(store: Store) -> None:
    _insert_effect(store, "ef-1", "launch:as-1")
    with pytest.raises(Exception, match="UNIQUE"):
        _insert_effect(store, "ef-2", "launch:as-1")


def test_due_effects_and_update_effect_round_trip(store: Store) -> None:
    _insert_effect(store, "ef-1", "launch:as-1", due=5)
    _insert_effect(store, "ef-2", "launch:as-2", due=50)
    due = store.due_effects(10)
    assert [e.id for e in due] == ["ef-1"]
    claimed = due[0].model_copy(update={"state": EffectState.RUNNING, "attempts": 1})
    assert store.update_effect(claimed) == claimed
    assert store.due_effects(10) == []
    assert store.count_effects("tk-1", EffectState.RUNNING) == 0


def test_concurrent_reader_only_sees_committed_state(tmp_path: Path) -> None:
    """B6 后半"并发读取"：独立连接与写事务并发——未提交内容对它不可见，提交后可见。"""
    handle = Store(tmp_path / "orchestration.db", clock=FixedClock())
    reader = sqlite3.connect(str(tmp_path / "orchestration.db"))
    reader.row_factory = sqlite3.Row
    try:
        handle.insert_ticket(_ticket("tk-1"))
        with handle.transaction():
            handle.insert_ticket(_ticket("tk-2"))
            seen = {row[0] for row in reader.execute("SELECT id FROM tickets").fetchall()}
            assert seen == {"tk-1"}  # 事务内未提交的 tk-2 不泄漏给并发读取
        seen = {row[0] for row in reader.execute("SELECT id FROM tickets").fetchall()}
        assert seen == {"tk-1", "tk-2"}  # 提交后立即可读
    finally:
        reader.close()
        handle.close()


OLD_ASSIGNMENT_DDL = (
    "CREATE TABLE assignments (id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL,"
    " attempt INTEGER NOT NULL, status TEXT NOT NULL, worker_run_id TEXT,"
    " started_at INTEGER, ended_at INTEGER, checkpoint_run_id TEXT, failure TEXT,"
    " worker_config_version TEXT, worker_config TEXT NOT NULL DEFAULT '{}',"
    " UNIQUE (ticket_id, attempt), UNIQUE (worker_run_id))"
)
OLD_INPUTS_DDL = (
    "CREATE TABLE inputs (id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL,"
    " sequence INTEGER NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,"
    " created_at INTEGER NOT NULL, delivered_at INTEGER, consumed_at INTEGER,"
    " UNIQUE (ticket_id, sequence))"
)


def test_m2_migration_adds_columns_and_backfills_state(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy.db"
    conn = sqlite3.connect(legacy)
    conn.execute(OLD_ASSIGNMENT_DDL)
    conn.execute(OLD_INPUTS_DDL)
    conn.execute(
        "INSERT INTO assignments (id,ticket_id,attempt,status) VALUES ('as-1','tk-1',1,'pending')"
    )
    conn.execute(
        "INSERT INTO inputs (id,ticket_id,sequence,kind,payload,created_at,consumed_at)"
        " VALUES ('in-1','tk-1',1,'text','x',1,9)"
    )
    conn.commit()
    conn.close()
    migrated = Store(legacy, clock=FixedClock())  # M1 形状的老库：重开即完成迁移
    try:
        columns = {row["name"] for row in migrated._conn.execute("PRAGMA table_info(assignments)")}
        assert "deadline_at" in columns
        assert migrated.get_assignment("as-1").deadline_at is None
        assert migrated.list_inputs("tk-1")[0].state == "consumed"  # 由 consumed_at 回填
    finally:
        migrated.close()


def test_update_input_and_get_effect_are_public(store: Store) -> None:
    item = TicketInput(id="in-1", ticket_id="tk-1", sequence=1, payload="x", created_at=1)
    store.insert_input(item)
    store.update_input(item.model_copy(update={"state": "consumed", "consumed_at": 2}))
    assert store.list_inputs("tk-1", pending_only=True) == []
    assert store.get_effect("ef-missing") is None
