"""人工监管的只读查询。只读：本模块不写库、不建 Ticket、不投递消息。

排序是产品契约的一部分（规格 2 的注意力预算）：待人工审批 → 失败 effect →
waiting Ticket → ready Ticket。时间线只返回已批准的结构化摘要，不返回 payload。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from stableagent.orchestration.model import (
    AssignmentStatus,
    Clock,
    SystemClock,
    TicketStatus,
)
from stableagent.supervisor.model import (
    SourceCursor,
    SubscriptionMode,
    Thread,
    ThreadKind,
)
from stableagent.supervisor.store import SupervisorStore


class AttentionItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    priority: int
    source: Literal["approval", "failed_effect", "waiting_ticket", "ready_ticket"]
    ticket_id: str | None = None
    target_id: str = ""
    needs: str = ""
    why: str = ""
    actions: tuple[str, ...] = ()
    ticket_link: str | None = None
    thread_link: str | None = None
    detail: str = ""


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
    object_id: str | None = None


class AssignmentSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    attempt: int
    status: AssignmentStatus
    started_at: int | None
    ended_at: int | None
    failure: str | None


class SubscriptionView(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    thread_key: str
    mode: SubscriptionMode
    created_at: int
    ended_at: int | None


class TicketDetail(BaseModel):
    model_config = ConfigDict(frozen=True)

    ticket: TicketSummary
    mandate_id: str
    assignments: list[AssignmentSummary]
    subscriptions: list[SubscriptionView]
    ticket_link: str | None
    thread_link: str | None
    timeline: list[TimelineEvent]


_OPEN_STATUSES = (
    TicketStatus.OPEN,
    TicketStatus.QUEUED,
    TicketStatus.RUNNING,
    TicketStatus.WAITING,
    TicketStatus.READY,
    TicketStatus.BLOCKED,
)

_ACTIONS_BY_SOURCE: dict[str, tuple[str, ...]] = {
    "approval": ("approve", "deny", "stop"),
    "failed_effect": ("retry", "stop"),
    "waiting_ticket": ("stop",),
    "ready_ticket": ("stop",),
}
_NEEDS_BY_SOURCE = {
    "approval": "approve or deny the pending external write",
    "failed_effect": "retry or stop the failed effect",
    "waiting_ticket": "reply to the ticket or stop it",
    "ready_ticket": "review the result or stop it",
}


class QueueCounts(BaseModel):
    model_config = ConfigDict(frozen=True)

    queued_tickets: int
    running_assignments: int
    due_effects: int
    failed_effects: int
    pending_approvals: int


class StatusSections(BaseModel):
    model_config = ConfigDict(frozen=True)

    attention: list[AttentionItem]
    source_health: list[SourceCursor]
    queue: QueueCounts


class BetaReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    triggers: dict[str, int]
    decisions: dict[str, int]
    decision_reasons: dict[str, int]
    would_accept: int
    tickets: dict[str, int]
    recovered_assignments: int
    approvals: dict[str, int]
    notification_counts: dict[str, int]
    effects: dict[str, int]
    retried_effects: int
    source_health: tuple[SourceCursor, ...]


def beta_report(store: SupervisorStore) -> BetaReport:
    snapshot = store.beta_snapshot()
    return BetaReport(
        triggers=snapshot.triggers,
        decisions=snapshot.decisions,
        decision_reasons=snapshot.decision_reasons,
        would_accept=snapshot.would_accept,
        tickets=snapshot.tickets,
        recovered_assignments=snapshot.recovered_assignments,
        approvals=snapshot.approvals,
        notification_counts=snapshot.notification_counts,
        effects=snapshot.effects,
        retried_effects=snapshot.retried_effects,
        source_health=snapshot.source_health,
    )


def thread_url(thread: Thread) -> str | None:
    """展示用链接：Issue/PR 指向具体条目，Review/Repository 按规格回落仓库 URL。"""
    base = f"https://github.com/{thread.repository}"
    if thread.kind is ThreadKind.ISSUE:
        return f"{base}/issues/{thread.external_ref.lstrip('#')}"
    if thread.kind is ThreadKind.PULL_REQUEST:
        return f"{base}/pull/{thread.external_ref.lstrip('#')}"
    return base


def comment_url(thread: Thread) -> str | None:
    """GitHub 评论要落在 Issue/PR 上；Review 回落到其 PR，仓库级 Thread 不草拟。"""
    base = f"https://github.com/{thread.repository}"
    if thread.kind is ThreadKind.ISSUE:
        return f"{base}/issues/{thread.external_ref.lstrip('#')}"
    if thread.kind is ThreadKind.PULL_REQUEST:
        return f"{base}/pull/{thread.external_ref.lstrip('#')}"
    if thread.kind is ThreadKind.REVIEW:
        tail = thread.key.split("#", 1)[1] if "#" in thread.key else ""
        number = tail.split(":", 1)[0]
        return f"{base}/pull/{number}" if number.isdigit() else None
    return None


def _live_thread(store: SupervisorStore, ticket_id: str) -> Thread | None:
    subscriptions = sorted(
        store.list_subscriptions(ticket_id=ticket_id, live_only=True),
        key=lambda item: (item.mode is not SubscriptionMode.OWN, item.created_at, item.id),
    )
    for subscription in subscriptions:
        thread = store.get_thread(subscription.thread_key)
        if thread is not None:
            return thread
    return None


def ticket_links(store: SupervisorStore, ticket_id: str) -> tuple[str | None, str | None]:
    thread = _live_thread(store, ticket_id)
    if thread is None:
        return None, None
    url = thread_url(thread)
    return url, url


def ticket_url(store: SupervisorStore, ticket_id: str) -> str | None:
    return ticket_links(store, ticket_id)[0]


class SupervisorViews:
    def __init__(self, store: SupervisorStore, *, clock: Clock | None = None) -> None:
        self._store = store
        self._clock = clock or SystemClock()

    def needs_attention(self) -> list[AttentionItem]:
        items: list[AttentionItem] = []
        for row in self._store.list_pending_approvals():
            ticket_link, thread_link = ticket_links(self._store, row["ticket_id"])
            items.append(
                AttentionItem(
                    priority=0,
                    source="approval",
                    ticket_id=row["ticket_id"],
                    target_id=row["id"],
                    needs=_NEEDS_BY_SOURCE["approval"],
                    why=f"{row['action']} requires approval",
                    actions=_ACTIONS_BY_SOURCE["approval"],
                    ticket_link=ticket_link,
                    thread_link=thread_link,
                    detail=f"approval pending: {row['action']}",
                )
            )
        for row in self._store.list_failed_effects():
            ticket_link, thread_link = ticket_links(self._store, row["ticket_id"])
            items.append(
                AttentionItem(
                    priority=1,
                    source="failed_effect",
                    ticket_id=row["ticket_id"],
                    target_id=row["effect_id"],
                    needs=_NEEDS_BY_SOURCE["failed_effect"],
                    why=row["last_error"] or "effect failed after retries",
                    actions=_ACTIONS_BY_SOURCE["failed_effect"],
                    ticket_link=ticket_link,
                    thread_link=thread_link,
                    detail=f"effect failed: {row['kind']}",
                )
            )
        for ticket in self._store.list_tickets(status=TicketStatus.WAITING):
            ticket_link, thread_link = ticket_links(self._store, ticket.id)
            items.append(
                AttentionItem(
                    priority=2,
                    source="waiting_ticket",
                    ticket_id=ticket.id,
                    target_id=ticket.id,
                    needs=_NEEDS_BY_SOURCE["waiting_ticket"],
                    why="ticket is waiting for human input",
                    actions=_ACTIONS_BY_SOURCE["waiting_ticket"],
                    ticket_link=ticket_link,
                    thread_link=thread_link,
                    detail="ticket waiting for human input",
                )
            )
        for ticket in self._store.list_tickets(status=TicketStatus.READY):
            ticket_link, thread_link = ticket_links(self._store, ticket.id)
            items.append(
                AttentionItem(
                    priority=3,
                    source="ready_ticket",
                    ticket_id=ticket.id,
                    target_id=ticket.id,
                    needs=_NEEDS_BY_SOURCE["ready_ticket"],
                    why="ticket finished and needs review",
                    actions=_ACTIONS_BY_SOURCE["ready_ticket"],
                    ticket_link=ticket_link,
                    thread_link=thread_link,
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

    def status_sections(self) -> StatusSections:
        return StatusSections(
            attention=self.needs_attention(),
            source_health=self.source_health(),
            queue=QueueCounts(
                queued_tickets=self._store.count_tickets(status=TicketStatus.QUEUED),
                running_assignments=self._store.count_assignments(status=AssignmentStatus.RUNNING),
                due_effects=self._store.count_due_effects(self._clock.now_ms()),
                failed_effects=len(self._store.list_failed_effects()),
                pending_approvals=len(self._store.list_pending_approvals()),
            ),
        )

    def thread_timeline(self, thread_key: str) -> list[TimelineEvent]:
        events: list[TimelineEvent] = []
        decisions: dict[str, TimelineEvent] = {}
        for trigger in self._store.list_triggers(thread_key=thread_key):
            events.append(
                TimelineEvent(
                    at=trigger.occurred_at,
                    kind="trigger",
                    summary=f"{trigger.kind.value}:{trigger.actor}",
                    object_id=trigger.id,
                )
            )
            for decision in self._store.list_decisions(trigger_id=trigger.id):
                decisions[decision.id] = TimelineEvent(
                    at=decision.created_at,
                    kind="decision",
                    summary=decision.outcome.value,
                    object_id=decision.id,
                )
        events.extend(decisions.values())
        return sorted(events, key=lambda event: (event.at, event.kind, event.summary))

    def ticket_timeline(self, ticket_id: str) -> list[TimelineEvent]:
        ticket = self._store.get_ticket(ticket_id)
        if ticket is None:
            raise KeyError(ticket_id)
        events = [
            TimelineEvent(
                at=report.created_at,
                kind="report",
                summary=report.summary,
                object_id=report.id,
            )
            for report in self._store.list_reports(ticket_id)
        ]
        events.extend(
            TimelineEvent(
                at=ticket_input.created_at,
                kind="input",
                summary=f"{ticket_input.kind}:{ticket_input.state}",
                object_id=ticket_input.id,
            )
            for ticket_input in self._store.list_inputs(ticket_id)
        )
        events.extend(
            TimelineEvent(
                at=decision.created_at,
                kind="decision",
                summary=decision.outcome.value,
                object_id=decision.id,
            )
            for decision in self._store.list_decisions(mandate_id=ticket.mandate_id)
        )
        return sorted(events, key=lambda event: (event.at, event.kind, event.summary))

    def ticket_detail(self, ticket_id: str) -> TicketDetail:
        ticket = self._store.get_ticket(ticket_id)
        if ticket is None:
            raise KeyError(ticket_id)
        ticket_link, thread_link = ticket_links(self._store, ticket_id)
        return TicketDetail(
            ticket=TicketSummary(
                id=ticket.id,
                title=ticket.title,
                status=ticket.status,
                created_at=ticket.created_at,
            ),
            mandate_id=ticket.mandate_id,
            assignments=[
                AssignmentSummary(
                    id=assignment.id,
                    attempt=assignment.attempt,
                    status=assignment.status,
                    started_at=assignment.started_at,
                    ended_at=assignment.ended_at,
                    failure=assignment.failure,
                )
                for assignment in self._store.list_assignments(ticket_id)
            ],
            subscriptions=[
                SubscriptionView(
                    id=subscription.id,
                    thread_key=subscription.thread_key,
                    mode=subscription.mode,
                    created_at=subscription.created_at,
                    ended_at=subscription.ended_at,
                )
                for subscription in self._store.list_subscriptions(ticket_id=ticket_id)
            ],
            ticket_link=ticket_link,
            thread_link=thread_link,
            timeline=self.ticket_timeline(ticket_id),
        )
