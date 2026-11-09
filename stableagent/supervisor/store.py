"""Supervisor 持久化。表、索引与 CRUD 全部在本包，orchestration 不知道 supervisor。

与 P4 Store 共用连接与 BEGIN IMMEDIATE，因此 Trigger/Decision/Subscription 和
Ticket/Assignment/Effect 可以在同一事务内落库（规格 2.1、ADR-06 决策 2/7）。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from stableagent.orchestration.model import Clock
from stableagent.orchestration.store import Store
from stableagent.supervisor import SUPERVISOR_SCHEMA_VERSION
from stableagent.supervisor.model import (
    Channel,
    ChannelKind,
    Decision,
    DecisionOutcome,
    DecisionStrength,
    SourceCursor,
    SourceHealth,
    Subscription,
    SubscriptionMode,
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
    "channels": (
        "CREATE TABLE IF NOT EXISTS channels ("
        " id TEXT PRIMARY KEY, thread_key TEXT NOT NULL, kind TEXT NOT NULL,"
        " external_ref TEXT NOT NULL,"
        " UNIQUE (thread_key, kind, external_ref))"
    ),
    "subscriptions": (
        "CREATE TABLE IF NOT EXISTS subscriptions ("
        " id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL, thread_key TEXT NOT NULL,"
        " mode TEXT NOT NULL, created_at INTEGER NOT NULL, ended_at INTEGER)"
    ),
    "decisions": (
        "CREATE TABLE IF NOT EXISTS decisions ("
        " id TEXT PRIMARY KEY, outcome TEXT NOT NULL, strength TEXT NOT NULL,"
        " reason_codes TEXT NOT NULL DEFAULT '[]', policy_version TEXT NOT NULL,"
        " mandate_id TEXT, request_digest TEXT NOT NULL, attempt INTEGER NOT NULL,"
        " created_at INTEGER NOT NULL, UNIQUE (request_digest, attempt))"
    ),
    "decision_triggers": (
        "CREATE TABLE IF NOT EXISTS decision_triggers ("
        " decision_id TEXT NOT NULL, trigger_id TEXT NOT NULL,"
        " PRIMARY KEY (decision_id, trigger_id))"
    ),
    "source_cursors": (
        "CREATE TABLE IF NOT EXISTS source_cursors ("
        " source TEXT PRIMARY KEY, cursor TEXT, last_success_at INTEGER,"
        " consecutive_failures INTEGER NOT NULL DEFAULT 0,"
        " health TEXT NOT NULL DEFAULT 'ok')"
    ),
}

_SUPERVISOR_INDEXES: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS triggers_received ON triggers (received_at, id)",
    "CREATE INDEX IF NOT EXISTS threads_last_trigger ON threads (last_trigger_at, key)",
    "CREATE INDEX IF NOT EXISTS subscriptions_thread ON subscriptions (thread_key, ended_at)",
    "CREATE UNIQUE INDEX IF NOT EXISTS subscriptions_live"
    " ON subscriptions (ticket_id, thread_key, mode) WHERE ended_at IS NULL",
    "CREATE INDEX IF NOT EXISTS decisions_created ON decisions (created_at, id)",
    "CREATE INDEX IF NOT EXISTS decisions_mandate ON decisions (mandate_id, created_at)",
    "CREATE INDEX IF NOT EXISTS decision_triggers_trigger ON decision_triggers (trigger_id)",
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


def _row_to_channel(row: sqlite3.Row) -> Channel:
    return Channel(
        id=row["id"],
        thread_key=row["thread_key"],
        kind=ChannelKind(row["kind"]),
        external_ref=row["external_ref"],
    )


def _row_to_subscription(row: sqlite3.Row) -> Subscription:
    return Subscription(
        id=row["id"],
        ticket_id=row["ticket_id"],
        thread_key=row["thread_key"],
        mode=SubscriptionMode(row["mode"]),
        created_at=row["created_at"],
        ended_at=row["ended_at"],
    )


def _row_to_source_cursor(row: sqlite3.Row) -> SourceCursor:
    return SourceCursor(
        source=row["source"],
        cursor=row["cursor"],
        last_success_at=row["last_success_at"],
        consecutive_failures=row["consecutive_failures"],
        health=SourceHealth(row["health"]),
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

    def insert_channel(self, channel: Channel) -> bool:
        cursor = self._conn.execute(
            "INSERT OR IGNORE INTO channels (id, thread_key, kind, external_ref)"
            " VALUES (?, ?, ?, ?)",
            (channel.id, channel.thread_key, channel.kind.value, channel.external_ref),
        )
        return int(cursor.rowcount) == 1

    def list_channels(self, thread_key: str) -> list[Channel]:
        rows = self._conn.execute(
            "SELECT * FROM channels WHERE thread_key = ? ORDER BY id ASC", (thread_key,)
        ).fetchall()
        return [_row_to_channel(row) for row in rows]

    def insert_subscription(self, subscription: Subscription) -> bool:
        cursor = self._conn.execute(
            "INSERT OR IGNORE INTO subscriptions"
            " (id, ticket_id, thread_key, mode, created_at, ended_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                subscription.id,
                subscription.ticket_id,
                subscription.thread_key,
                subscription.mode.value,
                subscription.created_at,
                subscription.ended_at,
            ),
        )
        return int(cursor.rowcount) == 1

    def end_subscription(self, subscription_id: str, *, ended_at: int) -> int:
        cursor = self._conn.execute(
            "UPDATE subscriptions SET ended_at = ? WHERE id = ? AND ended_at IS NULL",
            (ended_at, subscription_id),
        )
        return int(cursor.rowcount)

    def list_subscriptions(
        self,
        *,
        ticket_id: str | None = None,
        thread_key: str | None = None,
        live_only: bool = False,
    ) -> list[Subscription]:
        clauses, params = [], []
        if ticket_id is not None:
            clauses.append("ticket_id = ?")
            params.append(ticket_id)
        if thread_key is not None:
            clauses.append("thread_key = ?")
            params.append(thread_key)
        if live_only:
            clauses.append("ended_at IS NULL")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"SELECT * FROM subscriptions {where} ORDER BY created_at ASC, id ASC", params
        ).fetchall()
        return [_row_to_subscription(row) for row in rows]

    def insert_decision(self, decision: Decision) -> None:
        self._conn.execute(
            "INSERT INTO decisions"
            " (id, outcome, strength, reason_codes, policy_version, mandate_id,"
            " request_digest, attempt, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                decision.id,
                decision.outcome.value,
                decision.strength.value,
                json.dumps(decision.reason_codes, sort_keys=True, ensure_ascii=False),
                decision.policy_version,
                decision.mandate_id,
                decision.request_digest,
                decision.attempt,
                decision.created_at,
            ),
        )
        self._conn.executemany(
            "INSERT INTO decision_triggers (decision_id, trigger_id) VALUES (?, ?)",
            [(decision.id, trigger_id) for trigger_id in decision.trigger_ids],
        )

    def _row_to_decision(self, row: sqlite3.Row) -> Decision:
        trigger_ids = [
            str(r[0])
            for r in self._conn.execute(
                "SELECT trigger_id FROM decision_triggers WHERE decision_id = ?"
                " ORDER BY trigger_id ASC",
                (row["id"],),
            ).fetchall()
        ]
        return Decision(
            id=row["id"],
            trigger_ids=trigger_ids,
            outcome=DecisionOutcome(row["outcome"]),
            strength=DecisionStrength(row["strength"]),
            reason_codes=json.loads(row["reason_codes"]),
            policy_version=row["policy_version"],
            mandate_id=row["mandate_id"],
            request_digest=row["request_digest"],
            attempt=row["attempt"],
            created_at=row["created_at"],
        )

    def get_decision(self, decision_id: str) -> Decision | None:
        row = self._conn.execute("SELECT * FROM decisions WHERE id = ?", (decision_id,)).fetchone()
        return self._row_to_decision(row) if row else None

    def list_decisions(
        self,
        *,
        trigger_id: str | None = None,
        mandate_id: str | None = None,
        limit: int = 100,
    ) -> list[Decision]:
        clauses, params = [], []
        joins = ""
        if trigger_id is not None:
            joins = " JOIN decision_triggers dt ON dt.decision_id = d.id"
            clauses.append("dt.trigger_id = ?")
            params.append(trigger_id)
        if mandate_id is not None:
            clauses.append("d.mandate_id = ?")
            params.append(mandate_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"SELECT DISTINCT d.* FROM decisions d{joins} {where}"
            " ORDER BY d.created_at ASC, d.id ASC LIMIT ?",
            (*params, limit),
        ).fetchall()
        return [self._row_to_decision(row) for row in rows]

    def next_decision_attempt(self, request_digest: str) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(attempt), 0) + 1 FROM decisions WHERE request_digest = ?",
            (request_digest,),
        ).fetchone()
        return int(row[0])

    def find_accepted_decision(self, request_digest: str) -> Decision | None:
        row = self._conn.execute(
            "SELECT * FROM decisions WHERE request_digest = ? AND outcome = ?"
            " ORDER BY attempt ASC LIMIT 1",
            (request_digest, DecisionOutcome.ACCEPT.value),
        ).fetchone()
        return self._row_to_decision(row) if row else None

    def get_source_cursor(self, source: str) -> SourceCursor | None:
        row = self._conn.execute(
            "SELECT * FROM source_cursors WHERE source = ?", (source,)
        ).fetchone()
        return _row_to_source_cursor(row) if row else None

    def put_source_cursor(self, cursor: SourceCursor) -> None:
        self._conn.execute(
            "INSERT INTO source_cursors"
            " (source, cursor, last_success_at, consecutive_failures, health)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(source) DO UPDATE SET cursor = excluded.cursor,"
            " last_success_at = excluded.last_success_at,"
            " consecutive_failures = excluded.consecutive_failures,"
            " health = excluded.health",
            (
                cursor.source,
                cursor.cursor,
                cursor.last_success_at,
                cursor.consecutive_failures,
                cursor.health.value,
            ),
        )

    def list_source_cursors(self) -> list[SourceCursor]:
        rows = self._conn.execute("SELECT * FROM source_cursors ORDER BY source ASC").fetchall()
        return [_row_to_source_cursor(row) for row in rows]

    def list_pending_approvals(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM approval_requests WHERE status = 'pending'"
            " ORDER BY requested_at ASC, id ASC"
        ).fetchall()
        return [dict(row) for row in rows]

    def list_failed_effects(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT e.id AS effect_id, e.kind AS kind, e.last_error AS last_error,"
            " e.due_at AS due_at, a.ticket_id AS ticket_id"
            " FROM effects e"
            " JOIN assignments a ON json_extract(e.payload, '$.assignment_id') = a.id"
            " WHERE e.state = 'failed' ORDER BY e.due_at ASC, e.id ASC"
        ).fetchall()
        return [dict(row) for row in rows]
