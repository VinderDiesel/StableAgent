"""只读监管视图：优先级顺序、失败 effect 归属、时间线不泄露 payload。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stableagent.orchestration.model import (
    Assignment,
    AssignmentStatus,
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
from stableagent.supervisor.model import (
    Decision,
    DecisionOutcome,
    DecisionStrength,
    SourceCursor,
    SourceHealth,
    Subscription,
    SubscriptionMode,
    Thread,
    ThreadKind,
    Trigger,
    TriggerKind,
)
from stableagent.supervisor.store import SupervisorStore
from stableagent.supervisor.views import SupervisorViews, beta_report, thread_url
from tests.support import FixedClock


@pytest.fixture
def store(tmp_path: Path) -> SupervisorStore:
    return SupervisorStore(tmp_path / "p6.db", clock=FixedClock())


def _ticket(store: SupervisorStore, ticket_id: str, status: TicketStatus) -> Ticket:
    clock = FixedClock()
    store.insert_mandate(Mandate(id=f"md-{ticket_id}", granted_at=clock.now_ms()))
    ticket = Ticket(
        id=ticket_id,
        title=f"title-{ticket_id}",
        task="SECRET-TASK",
        created_at=clock.now_ms(),
        status=status,
        mandate_id=f"md-{ticket_id}",
    )
    store.insert_ticket(ticket)
    return ticket


def test_needs_attention_is_ordered_by_actionability(store: SupervisorStore) -> None:
    waiting = _ticket(store, "tk-wait", TicketStatus.WAITING)
    failed = _ticket(store, "tk-fail", TicketStatus.RUNNING)
    ready = _ticket(store, "tk-ready", TicketStatus.READY)
    store.insert_approval(
        id="ap-1",
        ticket_id=waiting.id,
        action="push",
        resource_digest="d",
        reason="r",
        requested_at=1,
    )
    store.insert_assignment(
        Assignment(id="as-1", ticket_id=failed.id, status=AssignmentStatus.RUNNING)
    )
    store.enqueue_effect(
        Effect(
            id="ef-1",
            kind=EffectKind.GITHUB_COMMENT,
            idempotency_key="k-1",
            payload={"assignment_id": "as-1"},
            state=EffectState.FAILED,
            due_at=1,
        )
    )

    items = SupervisorViews(store).needs_attention()

    assert [(i.priority, i.source, i.ticket_id) for i in items] == [
        (0, "approval", waiting.id),
        (1, "failed_effect", failed.id),
        (2, "waiting_ticket", waiting.id),
        (3, "ready_ticket", ready.id),
    ]


def test_open_tickets_excludes_terminal_statuses(store: SupervisorStore) -> None:
    _ticket(store, "tk-run", TicketStatus.RUNNING)
    _ticket(store, "tk-done", TicketStatus.COMPLETED)
    _ticket(store, "tk-cancel", TicketStatus.CANCELLED)
    assert [t.id for t in SupervisorViews(store).open_tickets()] == ["tk-run"]


def test_failed_effects_carry_ticket_identity(store: SupervisorStore) -> None:
    failed = _ticket(store, "tk-fail", TicketStatus.RUNNING)
    store.insert_assignment(
        Assignment(id="as-1", ticket_id=failed.id, status=AssignmentStatus.RUNNING)
    )
    store.enqueue_effect(
        Effect(
            id="ef-1",
            kind=EffectKind.GITHUB_COMMENT,
            idempotency_key="k-1",
            payload={"assignment_id": "as-1"},
            state=EffectState.FAILED,
            due_at=1,
        )
    )
    rows = SupervisorViews(store).failed_effects()
    assert [(r.ticket_id, r.effect_id) for r in rows] == [(failed.id, "ef-1")]


def test_source_health_lists_persisted_cursors(store: SupervisorStore) -> None:
    store.put_source_cursor(SourceCursor(source="github", cursor="9", health=SourceHealth.DEGRADED))
    assert [c.health for c in SupervisorViews(store).source_health()] == [SourceHealth.DEGRADED]


def test_thread_timeline_includes_triggers_and_decisions_without_payload(
    store: SupervisorStore,
) -> None:
    trigger = Trigger(
        id="tg-1",
        external_id="issue:1",
        kind=TriggerKind.ISSUE,
        actor="alice",
        thread_keys=["issue:acme/widgets#1"],
        occurred_at=10,
        received_at=11,
        payload={"body": "DO-NOT-LEAK"},
    )
    store.insert_trigger(trigger)
    store.insert_decision(
        Decision(
            id="dc-1",
            trigger_ids=["tg-1"],
            outcome=DecisionOutcome.OBSERVE,
            strength=DecisionStrength.INFERRED,
            policy_version="p6-v1",
            request_digest="d-1",
            created_at=12,
        )
    )

    events = SupervisorViews(store).thread_timeline("issue:acme/widgets#1")

    assert [e.kind for e in events] == ["trigger", "decision"]
    assert "DO-NOT-LEAK" not in json.dumps([e.model_dump() for e in events])


def test_ticket_timeline_uses_sanctioned_summaries_only(store: SupervisorStore) -> None:
    ticket = _ticket(store, "tk-1", TicketStatus.RUNNING)
    store.insert_report(
        Report(
            id="rp-1",
            ticket_id=ticket.id,
            assignment_id="as-1",
            kind=ReportKind.PROGRESS,
            summary="local-restricted exit=0 output=intact duration_ms=0",
            created_at=20,
        )
    )
    store.insert_input(
        TicketInput(
            id="in-1",
            ticket_id=ticket.id,
            sequence=1,
            payload="RAW-SECRET-INPUT",
            created_at=21,
        )
    )

    events = SupervisorViews(store).ticket_timeline(ticket.id)

    assert [e.kind for e in events] == ["report", "input"]
    rendered = json.dumps([e.model_dump() for e in events])
    assert "exit=0" in rendered
    assert "RAW-SECRET-INPUT" not in rendered and "SECRET-TASK" not in rendered


def test_status_sections_group_attention_health_and_queue(store: SupervisorStore) -> None:
    waiting = _ticket(store, "tk-wait", TicketStatus.WAITING)
    running = _ticket(store, "tk-run", TicketStatus.RUNNING)
    store.insert_approval(
        id="ap-1",
        ticket_id=waiting.id,
        action="comment_issue",
        resource_digest="d-1",
        reason="notification:attention",
        requested_at=1,
    )
    store.insert_assignment(
        Assignment(id="as-1", ticket_id=running.id, status=AssignmentStatus.RUNNING)
    )
    store.enqueue_effect(
        Effect(
            id="ef-1",
            kind=EffectKind.GITHUB_COMMENT,
            idempotency_key="k-1",
            payload={"assignment_id": "as-1"},
            state=EffectState.PENDING,
            due_at=0,
        )
    )
    store.put_source_cursor(SourceCursor(source="github", cursor="9"))

    sections = SupervisorViews(store, clock=FixedClock()).status_sections()

    assert [(item.priority, item.target_id) for item in sections.attention] == [
        (0, "ap-1"),
        (2, "tk-wait"),
    ]
    assert [cursor.source for cursor in sections.source_health] == ["github"]
    assert sections.queue.queued_tickets == 0
    assert sections.queue.running_assignments == 1
    assert sections.queue.due_effects == 1
    assert sections.queue.failed_effects == 0
    assert sections.queue.pending_approvals == 1


def test_attention_items_carry_targets_actions_and_links(store: SupervisorStore) -> None:
    waiting = _ticket(store, "tk-wait", TicketStatus.WAITING)
    store.upsert_thread(
        Thread(
            key="issue:o/r#1",
            repository="o/r",
            kind=ThreadKind.ISSUE,
            external_ref="#1",
            last_trigger_at=1,
        )
    )
    store.insert_subscription(
        Subscription(
            id="sb-1",
            ticket_id=waiting.id,
            thread_key="issue:o/r#1",
            mode=SubscriptionMode.OWN,
            created_at=1,
        )
    )
    store.insert_approval(
        id="ap-1",
        ticket_id=waiting.id,
        action="comment_issue",
        resource_digest="d-1",
        reason="notification:attention",
        requested_at=1,
    )

    items = SupervisorViews(store).needs_attention()
    approval = next(item for item in items if item.source == "approval")

    assert approval.target_id == "ap-1"
    assert approval.actions == ("approve", "deny", "stop")
    assert approval.needs == "approve or deny the pending external write"
    assert approval.why == "comment_issue requires approval"
    assert approval.ticket_link == "https://github.com/o/r/issues/1"
    assert approval.thread_link == approval.ticket_link


def test_attention_links_stay_empty_without_a_live_subscription(
    store: SupervisorStore,
) -> None:
    waiting = _ticket(store, "tk-wait", TicketStatus.WAITING)
    store.insert_approval(
        id="ap-1",
        ticket_id=waiting.id,
        action="comment_issue",
        resource_digest="d-1",
        reason="notification:attention",
        requested_at=1,
    )

    approval = next(
        item for item in SupervisorViews(store).needs_attention() if item.source == "approval"
    )

    assert approval.ticket_link is None and approval.thread_link is None


def test_thread_url_maps_issue_pull_request_review_and_repository() -> None:
    assert (
        thread_url(
            Thread(
                key="issue:o/r#7",
                repository="o/r",
                kind=ThreadKind.ISSUE,
                external_ref="#7",
                last_trigger_at=1,
            )
        )
        == "https://github.com/o/r/issues/7"
    )
    assert (
        thread_url(
            Thread(
                key="pr:o/r#8",
                repository="o/r",
                kind=ThreadKind.PULL_REQUEST,
                external_ref="#8",
                last_trigger_at=1,
            )
        )
        == "https://github.com/o/r/pull/8"
    )
    assert (
        thread_url(
            Thread(
                key="review:o/r#9:42",
                repository="o/r",
                kind=ThreadKind.REVIEW,
                external_ref="review:42",
                last_trigger_at=1,
            )
        )
        == "https://github.com/o/r"
    )
    assert (
        thread_url(
            Thread(
                key="repo:o/r",
                repository="o/r",
                kind=ThreadKind.REPOSITORY,
                external_ref="o/r",
                last_trigger_at=1,
            )
        )
        == "https://github.com/o/r"
    )


def test_ticket_detail_lists_assignments_subscriptions_and_timeline(
    store: SupervisorStore,
) -> None:
    ticket = _ticket(store, "tk-1", TicketStatus.RUNNING)
    store.insert_assignment(
        Assignment(id="as-1", ticket_id=ticket.id, attempt=1, status=AssignmentStatus.RUNNING)
    )
    store.upsert_thread(
        Thread(
            key="issue:o/r#1",
            repository="o/r",
            kind=ThreadKind.ISSUE,
            external_ref="#1",
            last_trigger_at=1,
        )
    )
    store.insert_subscription(
        Subscription(
            id="sb-1",
            ticket_id=ticket.id,
            thread_key="issue:o/r#1",
            mode=SubscriptionMode.OWN,
            created_at=1,
        )
    )
    store.insert_report(
        Report(
            id="rp-1",
            ticket_id=ticket.id,
            assignment_id="as-1",
            kind=ReportKind.PROGRESS,
            summary="local-restricted exit=0",
            created_at=20,
        )
    )

    detail = SupervisorViews(store).ticket_detail(ticket.id)

    assert detail.ticket.id == ticket.id
    assert detail.mandate_id == ticket.mandate_id
    assert [(item.id, item.attempt, item.status) for item in detail.assignments] == [
        ("as-1", 1, AssignmentStatus.RUNNING)
    ]
    assert [item.thread_key for item in detail.subscriptions] == ["issue:o/r#1"]
    assert detail.ticket_link == "https://github.com/o/r/issues/1"
    assert [event.kind for event in detail.timeline] == ["report"]
    assert detail.timeline[0].object_id == "rp-1"
    assert "SECRET-TASK" not in json.dumps(detail.model_dump())


def test_ticket_detail_unknown_ticket_raises_key_error(store: SupervisorStore) -> None:
    with pytest.raises(KeyError):
        SupervisorViews(store).ticket_detail("tk-missing")


def test_thread_timeline_events_carry_object_ids(store: SupervisorStore) -> None:
    trigger = Trigger(
        id="tg-1",
        external_id="issue:1",
        kind=TriggerKind.ISSUE,
        actor="alice",
        thread_keys=["issue:acme/widgets#1"],
        occurred_at=10,
        received_at=11,
        payload={"body": "DO-NOT-LEAK"},
    )
    store.insert_trigger(trigger)
    store.insert_decision(
        Decision(
            id="dc-1",
            trigger_ids=["tg-1"],
            outcome=DecisionOutcome.OBSERVE,
            strength=DecisionStrength.INFERRED,
            policy_version="p6-v1",
            request_digest="d-1",
            created_at=12,
        )
    )

    events = SupervisorViews(store).thread_timeline("issue:acme/widgets#1")

    assert [event.object_id for event in events] == ["tg-1", "dc-1"]


def test_beta_report_aggregates_rehearsal_metrics(store: SupervisorStore) -> None:
    _ticket(store, "tk-a", TicketStatus.QUEUED)
    _ticket(store, "tk-b", TicketStatus.FAILED)
    store.insert_trigger(
        Trigger(
            id="tg-1",
            external_id="github:o/r:issue:1",
            kind=TriggerKind.ISSUE,
            actor="alice",
            thread_keys=["issue:o/r#1"],
            occurred_at=1,
            received_at=2,
            payload={"body": "DO-NOT-LEAK"},
        )
    )
    store.insert_trigger(
        Trigger(
            id="tg-2",
            external_id="github:o/r:ci_status:9",
            kind=TriggerKind.CI_STATUS,
            actor="github",
            thread_keys=["repo:o/r"],
            occurred_at=3,
            received_at=4,
            payload={},
        )
    )
    for decision_id, digest, outcome, strength, reasons, trigger_ids in (
        ("dc-1", "d-1", DecisionOutcome.ACCEPT, DecisionStrength.EXPLICIT, ["admitted"], ["tg-1"]),
        (
            "dc-2",
            "d-2",
            DecisionOutcome.OBSERVE,
            DecisionStrength.INFERRED,
            ["inferred_candidate"],
            [],
        ),
        (
            "dc-3",
            "d-3",
            DecisionOutcome.OBSERVE,
            DecisionStrength.INFERRED,
            ["ci_context_only"],
            ["tg-2"],
        ),
        (
            "dc-4",
            "d-4",
            DecisionOutcome.OBSERVE,
            DecisionStrength.INFERRED,
            ["already_owned"],
            [],
        ),
    ):
        store.insert_decision(
            Decision(
                id=decision_id,
                trigger_ids=trigger_ids,
                outcome=outcome,
                strength=strength,
                reason_codes=reasons,
                policy_version="p6-v1",
                request_digest=digest,
                created_at=5,
            )
        )
    store.insert_assignment(Assignment(id="as-1", ticket_id="tk-a", attempt=1))
    store.insert_assignment(Assignment(id="as-2", ticket_id="tk-a", attempt=2))
    store.insert_approval(
        id="ap-1",
        ticket_id="tk-a",
        action="comment_issue",
        resource_digest="ra-1",
        reason="notification:accepted",
        requested_at=1,
    )
    store.insert_approval(
        id="ap-2",
        ticket_id="tk-a",
        action="comment_issue",
        resource_digest="ra-2",
        reason="notification:result",
        requested_at=2,
        status="approved",
        decided_by="alice",
        decided_at=3,
    )
    store.enqueue_effect(
        Effect(
            id="ef-1",
            kind=EffectKind.GITHUB_COMMENT,
            idempotency_key="k-1",
            state=EffectState.SUCCEEDED,
            attempts=2,
            due_at=1,
        )
    )
    store.enqueue_effect(
        Effect(
            id="ef-2",
            kind=EffectKind.LAUNCH_ASSIGNMENT,
            idempotency_key="k-2",
            state=EffectState.PENDING,
            due_at=1,
        )
    )
    store.put_source_cursor(SourceCursor(source="github", cursor="9", health=SourceHealth.OK))

    report = beta_report(store)

    assert report.triggers == {"issue": 1, "ci_status": 1}
    assert report.decisions == {"accept": 1, "observe": 3}
    assert report.decision_reasons == {
        "admitted": 1,
        "already_owned": 1,
        "ci_context_only": 1,
        "inferred_candidate": 1,
    }
    assert report.would_accept == 1
    assert report.tickets == {"queued": 1, "failed": 1}
    assert report.recovered_assignments == 1
    assert report.approvals == {"comment_issue:approved": 1, "comment_issue:pending": 1}
    assert report.notification_counts == {"tk-a": 2}
    assert report.effects == {"github_comment:succeeded": 1, "launch_assignment:pending": 1}
    assert report.retried_effects == 1
    assert [cursor.health for cursor in report.source_health] == [SourceHealth.OK]
    assert "DO-NOT-LEAK" not in report.model_dump_json()


def test_beta_report_on_empty_database_is_zero(store: SupervisorStore) -> None:
    report = beta_report(store)
    assert report.triggers == {}
    assert report.decisions == {}
    assert report.decision_reasons == {}
    assert report.would_accept == 0
    assert report.tickets == {}
    assert report.recovered_assignments == 0
    assert report.approvals == {}
    assert report.notification_counts == {}
    assert report.effects == {}
    assert report.retried_effects == 0
    assert report.source_health == ()
