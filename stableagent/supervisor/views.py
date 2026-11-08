"""人工监管的只读查询。只读：本模块不写库、不建 Ticket、不投递消息。

排序是产品契约的一部分（规格 2 的注意力预算）：待人工审批 → 失败 effect →
waiting Ticket → ready Ticket。时间线只返回已批准的结构化摘要，不返回 payload。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from stableagent.orchestration.model import TicketStatus
from stableagent.supervisor.model import SourceCursor
from stableagent.supervisor.store import SupervisorStore


class AttentionItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    priority: int
    source: Literal["approval", "failed_effect", "waiting_ticket", "ready_ticket"]
    ticket_id: str | None
    detail: str


class TicketSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    title: str
    status: TicketStatus
    created_at: int


class FailedEffectView(BaseModel):
    model_config = ConfigDict(frozen=True)

    effect_id: str
    kind: str
    ticket_id: str | None
    last_error: str | None
    due_at: int


class TimelineEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    at: int
    kind: Literal["trigger", "decision", "report", "input"]
    summary: str


_OPEN_STATUSES = (
    TicketStatus.OPEN,
    TicketStatus.QUEUED,
    TicketStatus.RUNNING,
    TicketStatus.WAITING,
    TicketStatus.READY,
    TicketStatus.BLOCKED,
)


class SupervisorViews:
    def __init__(self, store: SupervisorStore) -> None:
        self._store = store

    def needs_attention(self) -> list[AttentionItem]:
        items: list[AttentionItem] = []
        for row in self._store.list_pending_approvals():
            items.append(
                AttentionItem(
                    priority=0,
                    source="approval",
                    ticket_id=row["ticket_id"],
                    detail=f"approval pending: {row['action']}",
                )
            )
        for row in self._store.list_failed_effects():
            items.append(
                AttentionItem(
                    priority=1,
                    source="failed_effect",
                    ticket_id=row["ticket_id"],
                    detail=f"effect failed: {row['kind']}",
                )
            )
        for ticket in self._store.list_tickets(status=TicketStatus.WAITING):
            items.append(
                AttentionItem(
                    priority=2,
                    source="waiting_ticket",
                    ticket_id=ticket.id,
                    detail="ticket waiting for human input",
                )
            )
        for ticket in self._store.list_tickets(status=TicketStatus.READY):
            items.append(
                AttentionItem(
                    priority=3,
                    source="ready_ticket",
                    ticket_id=ticket.id,
                    detail="ticket ready for review",
                )
            )
        return sorted(items, key=lambda item: (item.priority, item.ticket_id or "", item.detail))

    def open_tickets(self) -> list[TicketSummary]:
        rows = []
        for status in _OPEN_STATUSES:
            rows.extend(self._store.list_tickets(status=status))
        return [
            TicketSummary(
                id=ticket.id,
                title=ticket.title,
                status=ticket.status,
                created_at=ticket.created_at,
            )
            for ticket in sorted(rows, key=lambda ticket: (ticket.created_at, ticket.id))
        ]

    def failed_effects(self) -> list[FailedEffectView]:
        return [
            FailedEffectView(
                effect_id=row["effect_id"],
                kind=row["kind"],
                ticket_id=row["ticket_id"],
                last_error=row["last_error"],
                due_at=row["due_at"],
            )
            for row in self._store.list_failed_effects()
        ]

    def source_health(self) -> list[SourceCursor]:
        return self._store.list_source_cursors()

    def thread_timeline(self, thread_key: str) -> list[TimelineEvent]:
        events: list[TimelineEvent] = []
        decisions: dict[str, TimelineEvent] = {}
        for trigger in self._store.list_triggers(thread_key=thread_key):
            events.append(
                TimelineEvent(
                    at=trigger.occurred_at,
                    kind="trigger",
                    summary=f"{trigger.kind.value}:{trigger.actor}",
                )
            )
            for decision in self._store.list_decisions(trigger_id=trigger.id):
                decisions[decision.id] = TimelineEvent(
                    at=decision.created_at,
                    kind="decision",
                    summary=decision.outcome.value,
                )
        events.extend(decisions.values())
        return sorted(events, key=lambda event: (event.at, event.kind, event.summary))

    def ticket_timeline(self, ticket_id: str) -> list[TimelineEvent]:
        ticket = self._store.get_ticket(ticket_id)
        if ticket is None:
            raise KeyError(ticket_id)
        events = [
            TimelineEvent(at=report.created_at, kind="report", summary=report.summary)
            for report in self._store.list_reports(ticket_id)
        ]
        events.extend(
            TimelineEvent(
                at=ticket_input.created_at,
                kind="input",
                summary=f"{ticket_input.kind}:{ticket_input.state}",
            )
            for ticket_input in self._store.list_inputs(ticket_id)
        )
        events.extend(
            TimelineEvent(at=decision.created_at, kind="decision", summary=decision.outcome.value)
            for decision in self._store.list_decisions(mandate_id=ticket.mandate_id)
        )
        return sorted(events, key=lambda event: (event.at, event.kind, event.summary))
