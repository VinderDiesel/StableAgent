"""生命周期通知：固定模板、限长与草拟（规格 WP-6.5）。

正文只由 Ticket 标题、对应种类的 Report `summary` 与稳定链接拼成；绝不读取
`Ticket.task`、`Trigger.payload`、`TicketInput.payload` 或日志（L4/L7）。
本模块不 import `runtime` / `integrations`，发送仍由 outbox + ApprovalGate 完成。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from stableagent.control.approval import ApprovalService
from stableagent.control.model import Action, ApprovalStatus, resource_digest
from stableagent.integrations.github_effects import github_resource
from stableagent.orchestration.model import (
    Clock,
    Effect,
    EffectKind,
    Report,
    ReportKind,
    Ticket,
    TicketStatus,
)
from stableagent.supervisor.model import SubscriptionMode
from stableagent.supervisor.store import SupervisorStore
from stableagent.supervisor.views import comment_url


class NotificationKind(str, Enum):
    ACCEPTED = "accepted"
    RESULT = "result"
    ATTENTION = "attention"
    FAILURE = "failure"


STATUS_KINDS: dict[TicketStatus, NotificationKind] = {
    TicketStatus.QUEUED: NotificationKind.ACCEPTED,
    TicketStatus.RUNNING: NotificationKind.ACCEPTED,
    TicketStatus.WAITING: NotificationKind.ATTENTION,
    TicketStatus.READY: NotificationKind.RESULT,
    TicketStatus.FAILED: NotificationKind.FAILURE,
}

REPORT_KINDS: dict[NotificationKind, ReportKind | None] = {
    NotificationKind.ACCEPTED: None,
    NotificationKind.RESULT: ReportKind.READY,
    NotificationKind.ATTENTION: ReportKind.WAITING_FOR_HUMAN,
    NotificationKind.FAILURE: ReportKind.FAILED,
}

_HEADERS = {
    NotificationKind.ACCEPTED: "Accepted",
    NotificationKind.RESULT: "Ready for review",
    NotificationKind.ATTENTION: "Waiting for you",
    NotificationKind.FAILURE: "Failed",
}


class NotificationConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = True
    max_chars: int = Field(default=400, ge=32)
    since_ms: int = Field(default=0, ge=0)


def latest_summary(reports: list[Report], kind: ReportKind) -> str | None:
    matching = [report for report in reports if report.kind is kind]
    if not matching:
        return None
    latest = max(matching, key=lambda report: (report.created_at, report.id))
    return latest.summary


def render_notification(
    ticket: Ticket,
    kind: NotificationKind,
    reports: list[Report],
    *,
    max_chars: int,
    url: str | None,
) -> str:
    lines = [f"[{_HEADERS[kind]}] {ticket.title}"]
    expected = REPORT_KINDS[kind]
    if expected is not None:
        summary = latest_summary(reports, expected)
        if summary:
            lines.append(summary)
    if url:
        lines.append(url)
    body = "\n".join(lines)
    if len(body) > max_chars:
        return body[: max_chars - 1] + "…"
    return body


@dataclass(frozen=True)
class NotificationDraft:
    ticket_id: str
    assignment_id: str
    kind: NotificationKind
    payload: dict[str, Any]
    digest: str


def notification_digest(payload: dict[str, Any]) -> str:
    return resource_digest(Action.COMMENT_ISSUE, github_resource(Action.COMMENT_ISSUE, payload))


def notification_effect(draft: NotificationDraft, *, effect_id: str, now: int) -> Effect:
    return Effect(
        id=effect_id,
        kind=EffectKind.GITHUB_COMMENT,
        idempotency_key=f"notify:{draft.ticket_id}:{draft.kind.value}",
        payload=dict(draft.payload),
        due_at=now,
    )


class NotificationDrafter:
    def __init__(
        self,
        store: SupervisorStore,
        approvals: ApprovalService,
        config: NotificationConfig,
        *,
        repository: str,
        clock: Clock,
    ) -> None:
        self._store = store
        self._approvals = approvals
        self._config = config
        self._repository = repository
        self._clock = clock

    def build(self, ticket_id: str) -> NotificationDraft | None:
        ticket = self._store.get_ticket(ticket_id)
        if ticket is None:
            return None
        kind = STATUS_KINDS.get(ticket.status)
        if kind is None:
            return None
        mandate = self._store.get_mandate(ticket.mandate_id)
        if (
            mandate is None
            or mandate.origin_kind != "github"
            or Action.COMMENT_ISSUE.value not in mandate.actions
        ):
            return None
        assignments = self._store.list_assignments(ticket_id)
        if not assignments:
            return None
        assignment = max(assignments, key=lambda item: item.attempt)
        url = self._ticket_comment_url(ticket_id)
        if url is None:
            return None
        body = render_notification(
            ticket,
            kind,
            self._store.list_reports(ticket_id),
            max_chars=self._config.max_chars,
            url=url,
        )
        payload = {
            "ticket_id": ticket.id,
            "assignment_id": assignment.id,
            "stable_id": mandate.scope,
            "repository": self._repository,
            "url": url,
            "body": body,
        }
        return NotificationDraft(
            ticket_id=ticket.id,
            assignment_id=assignment.id,
            kind=kind,
            payload=payload,
            digest=notification_digest(payload),
        )

    def draft_due(self) -> int:
        if not self._config.enabled:
            return 0
        drafted = 0
        for ticket in self._store.list_tickets_for_notifications(since_ms=self._config.since_ms):
            if self._silenced(ticket.id):
                continue
            draft = self.build(ticket.id)
            if draft is None or self._already_drafted(ticket.id, draft.digest):
                continue
            self._approvals.submit(
                ticket.id,
                Action.COMMENT_ISSUE,
                draft.digest,
                f"notification:{draft.kind.value}",
                assignment_id=draft.assignment_id,
            )
            drafted += 1
        return drafted

    def _ticket_comment_url(self, ticket_id: str) -> str | None:
        subscriptions = sorted(
            self._store.list_subscriptions(ticket_id=ticket_id, live_only=True),
            key=lambda item: (item.mode is not SubscriptionMode.OWN, item.created_at, item.id),
        )
        for subscription in subscriptions:
            thread = self._store.get_thread(subscription.thread_key)
            if thread is None:
                continue
            url = comment_url(thread)
            if url is not None:
                return url
        return None

    def _silenced(self, ticket_id: str) -> bool:
        return any(
            approval.action is Action.COMMENT_ISSUE and approval.status is ApprovalStatus.DENIED
            for approval in self._approvals.for_ticket(ticket_id)
        )

    def _already_drafted(self, ticket_id: str, digest: str) -> bool:
        return any(
            approval.action is Action.COMMENT_ISSUE and approval.resource_digest == digest
            for approval in self._approvals.for_ticket(ticket_id)
        )
