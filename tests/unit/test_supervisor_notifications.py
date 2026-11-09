"""WP-6.5：生命周期通知是纯函数；正文只取标题、结构化摘要与稳定链接。"""

from __future__ import annotations

from stableagent.orchestration.model import Report, ReportKind, Ticket
from stableagent.supervisor.notifications import (
    NotificationKind,
    latest_summary,
    render_notification,
)


def _ticket(*, title: str = "Fix parser") -> Ticket:
    return Ticket(
        id="tk-1",
        title=title,
        task="SECRET-TASK",
        created_at=1,
        mandate_id="md-1",
    )


def _report(kind: ReportKind, summary: str, *, created_at: int, report_id: str) -> Report:
    return Report(
        id=report_id,
        ticket_id="tk-1",
        assignment_id="as-1",
        kind=kind,
        summary=summary,
        created_at=created_at,
    )


def test_render_accepted_uses_title_and_link_only() -> None:
    body = render_notification(
        _ticket(),
        NotificationKind.ACCEPTED,
        [],
        max_chars=400,
        url="https://github.com/o/r/issues/1",
    )

    assert body == "[Accepted] Fix parser\nhttps://github.com/o/r/issues/1"
    assert "SECRET-TASK" not in body


def test_render_result_uses_latest_ready_summary() -> None:
    reports = [
        _report(ReportKind.READY, "older", created_at=2, report_id="rp-1"),
        _report(ReportKind.READY, "newer", created_at=3, report_id="rp-2"),
    ]

    body = render_notification(_ticket(), NotificationKind.RESULT, reports, max_chars=400, url=None)

    assert "newer" in body and "older" not in body


def test_latest_summary_ignores_other_report_kinds() -> None:
    reports = [
        _report(ReportKind.PROGRESS, "progress", created_at=9, report_id="rp-1"),
        _report(ReportKind.FAILED, "failed", created_at=8, report_id="rp-2"),
    ]

    assert latest_summary(reports, ReportKind.READY) is None


def test_render_truncates_to_max_chars_with_ellipsis() -> None:
    body = render_notification(
        _ticket(title="x" * 80),
        NotificationKind.ACCEPTED,
        [],
        max_chars=32,
        url=None,
    )

    assert len(body) == 32
    assert body.endswith("…")


def test_render_omits_summary_when_expected_report_is_missing() -> None:
    body = render_notification(
        _ticket(),
        NotificationKind.FAILURE,
        [],
        max_chars=400,
        url="https://github.com/o/r/issues/1",
    )

    assert body == "[Failed] Fix parser\nhttps://github.com/o/r/issues/1"
