"""编排层持久化。本模块只做持久化与约束，不做调度或策略判断（B7）。

事务模型：sqlite3 以 isolation_level=None 运行，事务由 transaction() 显式 BEGIN IMMEDIATE /
COMMIT / ROLLBACK 管理。事务体内禁止 await——驱动循环是单线程，同步段对协程不可分割，
因此"状态与 effect 同事务"是可证明性质而非约定（ADR-04 决策 3）。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from stableagent.orchestration import ORCHESTRATION_SCHEMA_VERSION
from stableagent.orchestration.errors import ReentrantTransactionError
from stableagent.orchestration.model import (
    Assignment,
    AssignmentStatus,
    Clock,
    Dependency,
    Effect,
    EffectKind,
    EffectState,
    Mandate,
    Report,
    ReportKind,
    Ticket,
    TicketInput,
    TicketStatus,
)

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
        " checkpoint_run_id TEXT, failure TEXT, deadline_at INTEGER,"
        " worker_config_version TEXT,"
        " worker_config TEXT NOT NULL DEFAULT '{}',"
        " UNIQUE (ticket_id, attempt), UNIQUE (worker_run_id))"
    ),
    "inputs": (
        "CREATE TABLE IF NOT EXISTS inputs ("
        " id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL, sequence INTEGER NOT NULL,"
        " kind TEXT NOT NULL, payload TEXT NOT NULL, created_at INTEGER NOT NULL,"
        " delivered_at INTEGER, consumed_at INTEGER, state TEXT NOT NULL DEFAULT 'pending',"
        " UNIQUE (ticket_id, sequence))"
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


def _row_to_mandate(row: sqlite3.Row) -> Mandate:
    return Mandate(
        id=row["id"],
        origin_kind=row["origin_kind"],
        requester=row["requester"],
        scope=row["scope"],
        policy_version=row["policy_version"],
        granted_at=row["granted_at"],
    )


def _row_to_ticket(row: sqlite3.Row) -> Ticket:
    return Ticket(
        id=row["id"],
        title=row["title"],
        task=row["task"],
        created_at=row["created_at"],
        status=TicketStatus(row["status"]),
        mandate_id=row["mandate_id"],
        parent_id=row["parent_id"],
        ended_at=row["ended_at"],
        queued_at=row["queued_at"],
        woken_at=row["woken_at"],
    )


def _row_to_assignment(row: sqlite3.Row) -> Assignment:
    return Assignment(
        id=row["id"],
        ticket_id=row["ticket_id"],
        attempt=row["attempt"],
        status=AssignmentStatus(row["status"]),
        worker_run_id=row["worker_run_id"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        checkpoint_run_id=row["checkpoint_run_id"],
        failure=row["failure"],
        deadline_at=row["deadline_at"],
        worker_config_version=row["worker_config_version"],
        worker_config=json.loads(row["worker_config"]),
    )


def _row_to_input(row: sqlite3.Row) -> TicketInput:
    return TicketInput(
        id=row["id"],
        ticket_id=row["ticket_id"],
        sequence=row["sequence"],
        kind=row["kind"],
        payload=row["payload"],
        created_at=row["created_at"],
        delivered_at=row["delivered_at"],
        consumed_at=row["consumed_at"],
        state=row["state"],
    )


def _row_to_report(row: sqlite3.Row) -> Report:
    return Report(
        id=row["id"],
        ticket_id=row["ticket_id"],
        assignment_id=row["assignment_id"],
        kind=ReportKind(row["kind"]),
        summary=row["summary"],
        created_at=row["created_at"],
        event_step=row["event_step"],
        details=json.loads(row["details"]),
    )


def _row_to_dependency(row: sqlite3.Row) -> Dependency:
    return Dependency(
        ticket_id=row["ticket_id"],
        depends_on_ticket_id=row["depends_on_ticket_id"],
        on_failure=row["on_failure"],
    )


def _row_to_effect(row: sqlite3.Row) -> Effect:
    return Effect(
        id=row["id"],
        kind=EffectKind(row["kind"]),
        idempotency_key=row["idempotency_key"],
        payload=json.loads(row["payload"]),
        state=EffectState(row["state"]),
        attempts=row["attempts"],
        due_at=row["due_at"],
        last_error=row["last_error"],
    )


class Store:
    def __init__(self, db_path: str | Path, *, clock: Clock) -> None:
        self._clock = clock
        self._conn = sqlite3.connect(str(db_path), isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._in_tx = False
        self._create_schema()
        self._migrate()

    def _migrate(self) -> None:
        """加列与索引（兼容扩展：全部带默认值）；老库的 inputs.state 由时间戳回填。

        预发布口径：ORCHESTRATION_SCHEMA_VERSION 保持 0.1.0——编排记录尚未对外承诺（ADR-04 决策 1）。
        """
        assignment_columns = {
            row["name"] for row in self._conn.execute("PRAGMA table_info(assignments)")
        }
        input_columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(inputs)")}
        with self.transaction():
            if "deadline_at" not in assignment_columns:
                self._conn.execute("ALTER TABLE assignments ADD COLUMN deadline_at INTEGER")
            if "state" not in input_columns:
                self._conn.execute(
                    "ALTER TABLE inputs ADD COLUMN state TEXT NOT NULL DEFAULT 'pending'"
                )
                self._conn.execute(
                    "UPDATE inputs SET state = 'consumed' WHERE consumed_at IS NOT NULL"
                )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS effects_due ON effects (due_at, kind, id)"
            )

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

    # 仓储方法：全部同步，假定已在调用方事务内或走 sqlite3 自动提交（Task 5）。

    def insert_mandate(self, mandate: Mandate) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO mandates"
            " (id, origin_kind, requester, scope, policy_version, granted_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                mandate.id,
                mandate.origin_kind,
                mandate.requester,
                mandate.scope,
                mandate.policy_version,
                mandate.granted_at,
            ),
        )

    def get_mandate(self, mandate_id: str) -> Mandate | None:
        row = self._conn.execute("SELECT * FROM mandates WHERE id = ?", (mandate_id,)).fetchone()
        return _row_to_mandate(row) if row else None

    def insert_ticket(self, ticket: Ticket) -> None:
        self._conn.execute(
            "INSERT INTO tickets"
            " (id, title, task, created_at, status, mandate_id, parent_id,"
            " ended_at, queued_at, woken_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ticket.id,
                ticket.title,
                ticket.task,
                ticket.created_at,
                ticket.status.value,
                ticket.mandate_id,
                ticket.parent_id,
                ticket.ended_at,
                ticket.queued_at,
                ticket.woken_at,
            ),
        )

    def update_ticket(self, ticket: Ticket) -> None:
        self._conn.execute(
            "UPDATE tickets SET title = ?, task = ?, created_at = ?, status = ?,"
            " mandate_id = ?, parent_id = ?, ended_at = ?, queued_at = ?, woken_at = ?"
            " WHERE id = ?",
            (
                ticket.title,
                ticket.task,
                ticket.created_at,
                ticket.status.value,
                ticket.mandate_id,
                ticket.parent_id,
                ticket.ended_at,
                ticket.queued_at,
                ticket.woken_at,
                ticket.id,
            ),
        )

    def get_ticket(self, ticket_id: str) -> Ticket | None:
        row = self._conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        return _row_to_ticket(row) if row else None

    def list_tickets(
        self,
        *,
        status: TicketStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Ticket]:
        clauses, params = [], []
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"SELECT * FROM tickets {where} ORDER BY created_at ASC, id ASC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
        return [_row_to_ticket(row) for row in rows]

    def list_runnable_tickets(self, now_ms: int) -> list[Ticket]:
        """优先级：被唤醒过的 > 已排队 > 新建（ADR-04 决策 7；E7 的 SQL 落点）。"""
        rows = self._conn.execute(
            "SELECT * FROM tickets WHERE status = 'queued'"
            " ORDER BY (woken_at IS NULL) ASC, queued_at ASC, created_at ASC"
        ).fetchall()
        return [_row_to_ticket(row) for row in rows]

    def insert_assignment(self, assignment: Assignment) -> None:
        self._conn.execute(
            "INSERT INTO assignments"
            " (id, ticket_id, attempt, status, worker_run_id, started_at, ended_at,"
            " checkpoint_run_id, failure, deadline_at, worker_config_version, worker_config)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                assignment.id,
                assignment.ticket_id,
                assignment.attempt,
                assignment.status.value,
                assignment.worker_run_id,
                assignment.started_at,
                assignment.ended_at,
                assignment.checkpoint_run_id,
                assignment.failure,
                assignment.deadline_at,
                assignment.worker_config_version,
                json.dumps(assignment.worker_config, sort_keys=True, ensure_ascii=False),
            ),
        )

    def update_assignment(self, assignment: Assignment) -> None:
        self._conn.execute(
            "UPDATE assignments SET ticket_id = ?, attempt = ?, status = ?,"
            " worker_run_id = ?, started_at = ?, ended_at = ?, checkpoint_run_id = ?,"
            " failure = ?, deadline_at = ?, worker_config_version = ?, worker_config = ?"
            " WHERE id = ?",
            (
                assignment.ticket_id,
                assignment.attempt,
                assignment.status.value,
                assignment.worker_run_id,
                assignment.started_at,
                assignment.ended_at,
                assignment.checkpoint_run_id,
                assignment.failure,
                assignment.deadline_at,
                assignment.worker_config_version,
                json.dumps(assignment.worker_config, sort_keys=True, ensure_ascii=False),
                assignment.id,
            ),
        )

    def get_assignment(self, assignment_id: str) -> Assignment | None:
        row = self._conn.execute(
            "SELECT * FROM assignments WHERE id = ?", (assignment_id,)
        ).fetchone()
        return _row_to_assignment(row) if row else None

    def list_assignments(self, ticket_id: str) -> list[Assignment]:
        rows = self._conn.execute(
            "SELECT * FROM assignments WHERE ticket_id = ? ORDER BY attempt ASC", (ticket_id,)
        ).fetchall()
        return [_row_to_assignment(row) for row in rows]

    def insert_report(self, report: Report) -> None:
        self._conn.execute(
            "INSERT INTO reports"
            " (id, ticket_id, assignment_id, kind, summary, created_at, event_step, details)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                report.id,
                report.ticket_id,
                report.assignment_id,
                report.kind.value,
                report.summary,
                report.created_at,
                report.event_step,
                json.dumps(report.details, sort_keys=True, ensure_ascii=False),
            ),
        )

    def list_reports(self, ticket_id: str) -> list[Report]:
        rows = self._conn.execute(
            "SELECT * FROM reports WHERE ticket_id = ? ORDER BY created_at ASC, id ASC",
            (ticket_id,),
        ).fetchall()
        return [_row_to_report(row) for row in rows]

    def insert_input(self, ticket_input: TicketInput) -> None:
        self._conn.execute(
            "INSERT INTO inputs"
            " (id, ticket_id, sequence, kind, payload, created_at,"
            " delivered_at, consumed_at, state)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ticket_input.id,
                ticket_input.ticket_id,
                ticket_input.sequence,
                ticket_input.kind,
                ticket_input.payload,
                ticket_input.created_at,
                ticket_input.delivered_at,
                ticket_input.consumed_at,
                ticket_input.state,
            ),
        )

    def update_input(self, ticket_input: TicketInput) -> TicketInput:
        self._conn.execute(
            "UPDATE inputs SET ticket_id = ?, sequence = ?, kind = ?, payload = ?,"
            " created_at = ?, delivered_at = ?, consumed_at = ?, state = ? WHERE id = ?",
            (
                ticket_input.ticket_id,
                ticket_input.sequence,
                ticket_input.kind,
                ticket_input.payload,
                ticket_input.created_at,
                ticket_input.delivered_at,
                ticket_input.consumed_at,
                ticket_input.state,
                ticket_input.id,
            ),
        )
        return ticket_input

    def list_inputs(self, ticket_id: str, *, pending_only: bool = False) -> list[TicketInput]:
        where = "ticket_id = ? AND state = 'pending'" if pending_only else "ticket_id = ?"
        rows = self._conn.execute(
            f"SELECT * FROM inputs WHERE {where} ORDER BY sequence ASC", (ticket_id,)
        ).fetchall()
        return [_row_to_input(row) for row in rows]

    def insert_dependency(self, dependency: Dependency) -> None:
        self._conn.execute(
            "INSERT INTO dependencies (ticket_id, depends_on_ticket_id, on_failure)"
            " VALUES (?, ?, ?)",
            (
                dependency.ticket_id,
                dependency.depends_on_ticket_id,
                dependency.on_failure,
            ),
        )

    def list_dependencies(self, ticket_id: str) -> list[Dependency]:
        rows = self._conn.execute(
            "SELECT * FROM dependencies WHERE ticket_id = ? ORDER BY depends_on_ticket_id ASC",
            (ticket_id,),
        ).fetchall()
        return [_row_to_dependency(row) for row in rows]

    def list_all_dependencies(self) -> list[Dependency]:
        rows = self._conn.execute(
            "SELECT * FROM dependencies ORDER BY ticket_id ASC, depends_on_ticket_id ASC"
        ).fetchall()
        return [_row_to_dependency(row) for row in rows]

    def update_effect(self, effect: Effect) -> Effect:
        self._conn.execute(
            "UPDATE effects SET kind = ?, idempotency_key = ?, payload = ?, state = ?,"
            " attempts = ?, due_at = ?, last_error = ? WHERE id = ?",
            (
                effect.kind.value,
                effect.idempotency_key,
                json.dumps(effect.payload, sort_keys=True, ensure_ascii=False),
                effect.state.value,
                effect.attempts,
                effect.due_at,
                effect.last_error,
                effect.id,
            ),
        )
        return effect

    def _effect_row(self, effect_id: str) -> Effect | None:
        return self.get_effect(effect_id)

    def get_effect(self, effect_id: str) -> Effect | None:
        row = self._conn.execute("SELECT * FROM effects WHERE id = ?", (effect_id,)).fetchone()
        return _row_to_effect(row) if row else None

    def _effect_by_key(self, kind: EffectKind, idempotency_key: str) -> Effect | None:
        row = self._conn.execute(
            "SELECT * FROM effects WHERE kind = ? AND idempotency_key = ?",
            (kind.value, idempotency_key),
        ).fetchone()
        return _row_to_effect(row) if row else None

    def enqueue_effect(self, effect: Effect) -> Effect:
        """同 (kind, idempotency_key) 的 pending/retry_wait 行只更新 payload/due_at；
        已进终态的行原样返回，绝不复活——幂等靠唯一键，不靠内存标志（C3）。"""
        existing = self._effect_by_key(effect.kind, effect.idempotency_key)
        if existing is not None and existing.state in (
            EffectState.SUCCEEDED,
            EffectState.FAILED,
            EffectState.CANCELLED,
        ):
            return existing
        if existing is not None:
            self._conn.execute(
                "UPDATE effects SET payload = ?, due_at = ? WHERE id = ?",
                (
                    json.dumps(effect.payload, sort_keys=True, ensure_ascii=False),
                    effect.due_at,
                    existing.id,
                ),
            )
            reloaded = self._effect_row(existing.id)
            assert reloaded is not None
            return reloaded
        self._conn.execute(
            "INSERT INTO effects (id,kind,idempotency_key,payload,state,attempts,due_at,last_error)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (
                effect.id,
                effect.kind.value,
                effect.idempotency_key,
                json.dumps(effect.payload, sort_keys=True, ensure_ascii=False),
                effect.state.value,
                effect.attempts,
                effect.due_at,
                effect.last_error,
            ),
        )
        return effect

    def reclaim_running_effects(self, now_ms: int) -> int:
        """崩溃时正在跑的 effect 退回 pending：幂等键保证不会重复副作用（C5）。"""
        cursor = self._conn.execute(
            "UPDATE effects SET state = 'pending', due_at = ? WHERE state = 'running'", (now_ms,)
        )
        return int(cursor.rowcount)

    def due_effects(self, now_ms: int, *, limit: int = 10) -> list[Effect]:
        rows = self._conn.execute(
            "SELECT * FROM effects WHERE state IN ('pending','retry_wait') AND due_at <= ?"
            " ORDER BY due_at ASC, id ASC LIMIT ?",
            (now_ms, limit),
        ).fetchall()
        return [_row_to_effect(row) for row in rows]

    def count_effects(self, ticket_id: str, state: EffectState) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM effects e"
            " JOIN assignments a ON json_extract(e.payload, '$.assignment_id') = a.id"
            " WHERE a.ticket_id = ? AND e.state = ?",
            (ticket_id, state.value),
        ).fetchone()
        return int(row[0])

    def close(self) -> None:
        self._conn.close()
