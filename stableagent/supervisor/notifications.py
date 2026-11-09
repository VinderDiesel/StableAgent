"""生命周期通知：固定模板、限长与草拟（规格 WP-6.5）。

正文只由 Ticket 标题、对应种类的 Report `summary` 与稳定链接拼成；绝不读取
`Ticket.task`、`Trigger.payload`、`TicketInput.payload` 或日志（L4/L7）。
本模块不 import `runtime` / `integrations`，发送仍由 outbox + ApprovalGate 完成。
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from stableagent.orchestration.model import Report, ReportKind, Ticket, TicketStatus


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
