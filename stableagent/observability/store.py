"""ObservabilityStore：在 P4 Store 连接上追加 append-only domain_events。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from stableagent.observability import OBSERVABILITY_SCHEMA_VERSION
from stableagent.observability.events import DomainEvent, DomainEventKind
from stableagent.orchestration.model import Clock
from stableagent.orchestration.store import Store

_DOMAIN_EVENTS_DDL = (
    "CREATE TABLE IF NOT EXISTS domain_events ("
    " id TEXT PRIMARY KEY,"
    " timestamp INTEGER NOT NULL,"
    " tenant_id TEXT NOT NULL,"
    " kind TEXT NOT NULL,"
    " outcome TEXT NOT NULL,"
    " reason_code TEXT,"
    " ticket_id TEXT,"
    " assignment_id TEXT,"
    " run_id TEXT,"
    " effect_id TEXT,"
    " trigger_id TEXT,"
    " decision_id TEXT,"
    " payload_digest TEXT NOT NULL,"
    " attrs TEXT NOT NULL DEFAULT '{}')"
)

_DOMAIN_EVENTS_INDEXES = (
    "CREATE INDEX IF NOT EXISTS domain_events_ts ON domain_events (timestamp, id)",
    "CREATE INDEX IF NOT EXISTS domain_events_ticket ON domain_events (ticket_id, timestamp)",
    "CREATE INDEX IF NOT EXISTS domain_events_kind ON domain_events (kind, timestamp)",
    "CREATE INDEX IF NOT EXISTS domain_events_tenant ON domain_events (tenant_id, timestamp)",
)


def _row_to_event(row: sqlite3.Row) -> DomainEvent:
    return DomainEvent(
        id=row["id"],
        timestamp=row["timestamp"],
        tenant_id=row["tenant_id"],
        kind=DomainEventKind(row["kind"]),
        outcome=row["outcome"],
        reason_code=row["reason_code"],
        ticket_id=row["ticket_id"],
        assignment_id=row["assignment_id"],
        run_id=row["run_id"],
        effect_id=row["effect_id"],
        trigger_id=row["trigger_id"],
        decision_id=row["decision_id"],
        payload_digest=row["payload_digest"],
        attrs=json.loads(row["attrs"]),
    )


class ObservabilityStore(Store):
    """共享 SQLite：只加表与 marker，不改既有编排/授权表。"""

    def __init__(self, db_path: str | Path, *, clock: Clock) -> None:
        super().__init__(db_path, clock=clock)
        with self.transaction():
            self._conn.execute(_DOMAIN_EVENTS_DDL)
            for ddl in _DOMAIN_EVENTS_INDEXES:
                self._conn.execute(ddl)
            self._conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (OBSERVABILITY_SCHEMA_VERSION, self._clock.now_ms()),
            )

    def insert_domain_event(self, event: DomainEvent) -> None:
        self._conn.execute(
            "INSERT INTO domain_events"
            " (id, timestamp, tenant_id, kind, outcome, reason_code,"
            "  ticket_id, assignment_id, run_id, effect_id, trigger_id, decision_id,"
            "  payload_digest, attrs)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event.id,
                event.timestamp,
                event.tenant_id,
                event.kind.value,
                event.outcome,
                event.reason_code,
                event.ticket_id,
                event.assignment_id,
                event.run_id,
                event.effect_id,
                event.trigger_id,
                event.decision_id,
                event.payload_digest,
                json.dumps(event.attrs, ensure_ascii=False, separators=(",", ":")),
            ),
        )

    def list_domain_events(
        self,
        *,
        ticket_id: str | None = None,
        kind: DomainEventKind | None = None,
        tenant_id: str | None = None,
        limit: int = 100,
    ) -> list[DomainEvent]:
        clauses: list[str] = []
        params: list[Any] = []
        if ticket_id is not None:
            clauses.append("ticket_id = ?")
            params.append(ticket_id)
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind.value)
        if tenant_id is not None:
            clauses.append("tenant_id = ?")
            params.append(tenant_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"SELECT * FROM domain_events {where}"
            " ORDER BY timestamp ASC, id ASC LIMIT ?",
            [*params, limit],
        ).fetchall()
        return [_row_to_event(row) for row in rows]
