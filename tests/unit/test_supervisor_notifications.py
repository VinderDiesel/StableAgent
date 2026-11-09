"""WP-6.5：生命周期通知是纯函数；正文只取标题、结构化摘要与稳定链接。"""

from __future__ import annotations

from pathlib import Path

import pytest

from stableagent.control.approval import ApprovalService
from stableagent.control.model import Action, ApprovalStatus
from stableagent.orchestration.model import (
    Assignment,
    Mandate,
    Report,
    ReportKind,
    Ticket,
    TicketStatus,
)
from stableagent.supervisor.model import (
    Subscription,
    SubscriptionMode,
    Thread,
    ThreadKind,
)
from stableagent.supervisor.notifications import (
    NotificationConfig,
    NotificationDrafter,
    NotificationKind,
    latest_summary,
    render_notification,
)
from stableagent.supervisor.store import SupervisorStore
from tests.support import FixedClock, SequenceIds


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


@pytest.fixture
def env(tmp_path: Path):
    clock = FixedClock()
    ids = SequenceIds()
    store = SupervisorStore(tmp_path / "p6.db", clock=clock)
    approvals = ApprovalService(store, clock=clock, ids=ids)
    return store, approvals, clock


def _seed_ticket(
    store: SupervisorStore,
    *,
    ticket_id: str = "tk-1",
    status: TicketStatus = TicketStatus.RUNNING,
    created_at: int = 1,
    kind: ThreadKind = ThreadKind.ISSUE,
    external_ref: str = "#1",
    subscription: bool = True,
    assignment: bool = True,
) -> Ticket:
    suffix = ticket_id.removeprefix("tk-")
    mandate_id = f"md-{suffix}"
    store.insert_mandate(
        Mandate(
            id=mandate_id,
            origin_kind="github",
            scope="github:o/r:issue:1",
            granted_at=1,
            repository="o/r",
            actions=["comment_issue"],
        )
    )
    ticket = Ticket(
        id=ticket_id,
        title="Fix parser",
        task="SECRET-TASK",
        created_at=created_at,
        status=status,
        mandate_id=mandate_id,
    )
    store.insert_ticket(ticket)
    if assignment:
        store.insert_assignment(Assignment(id=f"as-{suffix}", ticket_id=ticket_id, attempt=1))
    if subscription:
        key = (
            f"issue:o/r#{external_ref.lstrip('#')}"
            if kind is ThreadKind.ISSUE
            else f"pr:o/r#{external_ref.lstrip('#')}"
        )
        store.upsert_thread(
            Thread(
                key=key,
                repository="o/r",
                kind=kind,
                external_ref=external_ref,
                last_trigger_at=1,
            )
        )
        store.insert_subscription(
            Subscription(
                id=f"sb-{suffix}",
                ticket_id=ticket_id,
                thread_key=key,
                mode=SubscriptionMode.OWN,
                created_at=1,
            )
        )
    return ticket


def test_draft_due_submits_one_pending_comment_approval(env) -> None:
    store, approvals, clock = env
    _seed_ticket(store)
    drafter = NotificationDrafter(
        store,
        approvals,
        NotificationConfig(enabled=True, max_chars=400, since_ms=0),
        repository="o/r",
        clock=clock,
    )

    assert drafter.draft_due() == 1

    pending = approvals.for_ticket("tk-1", status=ApprovalStatus.PENDING)
    assert len(pending) == 1
    approval = pending[0]
    assert approval.action is Action.COMMENT_ISSUE
    assert approval.reason == "notification:accepted"
    assert approval.assignment_id == "as-1"
    draft = drafter.build("tk-1")
    assert draft is not None
    assert draft.digest == approval.resource_digest
    assert draft.payload == {
        "ticket_id": "tk-1",
        "assignment_id": "as-1",
        "stable_id": "github:o/r:issue:1",
        "repository": "o/r",
        "url": "https://github.com/o/r/issues/1",
        "body": "[Accepted] Fix parser\nhttps://github.com/o/r/issues/1",
    }
    assert "SECRET-TASK" not in draft.payload["body"]


def test_draft_due_is_idempotent_across_steps(env) -> None:
    store, approvals, clock = env
    _seed_ticket(store)
    drafter = NotificationDrafter(
        store,
        approvals,
        NotificationConfig(),
        repository="o/r",
        clock=clock,
    )

    assert drafter.draft_due() == 1
    assert drafter.draft_due() == 0
    assert len(approvals.for_ticket("tk-1")) == 1


def test_denied_notification_silences_the_ticket(env) -> None:
    store, approvals, clock = env
    _seed_ticket(store)
    drafter = NotificationDrafter(
        store, approvals, NotificationConfig(), repository="o/r", clock=clock
    )
    assert drafter.draft_due() == 1
    approval = approvals.for_ticket("tk-1", status=ApprovalStatus.PENDING)[0]
    approvals.decide(approval.id, False, "alice")

    assert drafter.draft_due() == 0
    assert approvals.for_ticket("tk-1", status=ApprovalStatus.PENDING) == []


def test_draft_due_skips_without_live_subscription_or_assignment(env) -> None:
    store, approvals, clock = env
    _seed_ticket(store, ticket_id="tk-nosub", subscription=False)
    _seed_ticket(store, ticket_id="tk-noas", assignment=False)
    drafter = NotificationDrafter(
        store, approvals, NotificationConfig(), repository="o/r", clock=clock
    )

    assert drafter.draft_due() == 0


def test_draft_due_respects_since_ms(env) -> None:
    store, approvals, clock = env
    _seed_ticket(store, created_at=1)
    drafter = NotificationDrafter(
        store,
        approvals,
        NotificationConfig(since_ms=100),
        repository="o/r",
        clock=clock,
    )

    assert drafter.draft_due() == 0


def test_draft_due_requires_github_mandate_with_comment_action(env) -> None:
    store, approvals, clock = env
    _seed_ticket(store)
    store._conn.execute("UPDATE mandates SET actions = '[]' WHERE id = 'md-1'")
    drafter = NotificationDrafter(
        store, approvals, NotificationConfig(), repository="o/r", clock=clock
    )

    assert drafter.draft_due() == 0
