"""WP-6.3：命令层权限、context 订阅与人工 stop。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from stableagent.control.approval import ApprovalService
from stableagent.control.model import Action, ApprovalStatus
from stableagent.control.policy import StaticPolicy, StaticPolicyConfig
from stableagent.orchestration.model import (
    Effect,
    EffectKind,
    EffectState,
    Report,
    ReportKind,
    TicketStatus,
)
from stableagent.supervisor.assessor import ObserveOnlyAssessor
from stableagent.supervisor.model import (
    AutonomyMode,
    DecisionOutcome,
    SubscriptionMode,
    Thread,
    ThreadKind,
    ThreadStatus,
    Trigger,
    TriggerKind,
)
from stableagent.supervisor.router import (
    RouterConfig,
    RouterNotFoundError,
    RouterPermissionError,
    RouterService,
    RouterStateError,
)
from stableagent.supervisor.service import SupervisorService
from stableagent.supervisor.store import SupervisorStore
from tests.support import FixedClock, SequenceIds


def _thread(key: str = "issue:o/r#1") -> Thread:
    kind = ThreadKind.PULL_REQUEST if key.startswith("pr:") else ThreadKind.ISSUE
    ref = key.rsplit("#", 1)[-1]
    return Thread(
        key=key,
        repository="o/r",
        kind=kind,
        external_ref=f"#{ref}",
        last_trigger_at=1,
        status=ThreadStatus.OPEN,
    )


def _trigger(
    *,
    external_id: str = "github:o/r:issue:1",
    thread_keys: list[str] | None = None,
    text: str | None = None,
) -> Trigger:
    payload: dict[str, Any] = {"resource": {"number": 1}, "deleted": False}
    if text is not None:
        payload["request"] = {"text": text, "mentioned": True, "url": None}
    return Trigger(
        id=f"tg-{external_id}",
        external_id=external_id,
        kind=TriggerKind.ISSUE,
        actor="alice",
        thread_keys=thread_keys or ["issue:o/r#1"],
        occurred_at=1,
        received_at=1,
        payload=payload,
    )


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock()


@pytest.fixture
def ids() -> SequenceIds:
    return SequenceIds()


@pytest.fixture
def store(tmp_path: Path, clock: FixedClock) -> SupervisorStore:
    return SupervisorStore(tmp_path / "p6.db", clock=clock)


@pytest.fixture
def make_service(store: SupervisorStore, clock: FixedClock, ids: SequenceIds):
    def build(*, audit: Any = None) -> SupervisorService:
        return _build_service(store, clock, ids, audit=audit)

    return build


@pytest.fixture
def service(make_service) -> SupervisorService:
    return make_service()


def _build_service(
    store: SupervisorStore, clock: FixedClock, ids: SequenceIds, *, audit: Any = None
) -> SupervisorService:
    policy = StaticPolicy(
        StaticPolicyConfig(
            repository="o/r",
            actors=frozenset({"alice"}),
            grantable_actions=frozenset({Action.READ_REPOSITORY, Action.RUN_COMMAND}),
            allowed_commands=["pytest"],
            default_validity_ms=60_000,
        ),
        clock=clock,
        ids=ids,
    )
    router = RouterService(
        store,
        policy,
        ObserveOnlyAssessor(),
        RouterConfig(
            repository="o/r",
            mode=AutonomyMode.OBSERVE,
            operators=frozenset({"alice"}),
            actions=("read_repository", "run_command"),
            allowed_commands=("pytest",),
        ),
        clock=clock,
        ids=ids,
    )
    approvals = ApprovalService(store, clock=clock, ids=ids)
    return SupervisorService(
        store,
        policy,
        router,
        clock=clock,
        ids=ids,
        audit=audit,
        approvals=approvals,
    )


def _accept(service: SupervisorService, store: SupervisorStore):
    store.upsert_thread(_thread())
    reception = service.receive(_trigger(text="fix it"))
    assert reception.ticket_id is not None
    ticket = store.get_ticket(reception.ticket_id)
    assert ticket is not None
    return ticket


def test_non_operators_cannot_attach_or_stop(service, store) -> None:
    ticket = _accept(service, store)
    with pytest.raises(RouterPermissionError):
        service.attach(ticket.id, thread_key="issue:o/r#2", actor="mallory")
    with pytest.raises(RouterPermissionError):
        service.stop(ticket.id, actor="mallory")
    with pytest.raises(RouterPermissionError):
        service.unsubscribe(ticket.id, thread_key="issue:o/r#1", actor="mallory")


def test_attach_is_an_idempotent_context_subscription(service, store) -> None:
    ticket = _accept(service, store)
    store.upsert_thread(_thread("issue:o/r#2"))
    first = service.attach(ticket.id, thread_key="issue:o/r#2", actor="alice")
    second = service.attach(ticket.id, thread_key="issue:o/r#2", actor="alice")
    assert first.id == second.id
    assert first.mode is SubscriptionMode.CONTEXT
    assert len(store.list_subscriptions(ticket_id=ticket.id, thread_key="issue:o/r#2")) == 1


def test_subscribe_uses_the_trigger_primary_thread(service, store) -> None:
    ticket = _accept(service, store)
    store.upsert_thread(_thread("issue:o/r#2"))
    trigger = _trigger(external_id="github:o/r:issue:2", thread_keys=["issue:o/r#2"])
    store.insert_trigger(trigger)
    subscription = service.subscribe(ticket.id, trigger_id=trigger.id, actor="alice")
    assert subscription.thread_key == "issue:o/r#2"
    assert subscription.mode is SubscriptionMode.CONTEXT


def test_unsubscribe_ends_live_subscriptions_and_stops_fanout(service, store) -> None:
    ticket = _accept(service, store)
    store.upsert_thread(_thread("issue:o/r#2"))
    service.attach(ticket.id, thread_key="issue:o/r#2", actor="alice")
    assert service.unsubscribe(ticket.id, thread_key="issue:o/r#2", actor="alice") == 1
    before = len(store.list_inputs(ticket.id))
    reception = service.receive(
        _trigger(external_id="github:o/r:issue:3", thread_keys=["issue:o/r#2"])
    )
    assert reception.fanned_out == 0
    assert len(store.list_inputs(ticket.id)) == before


def test_stop_cancels_ticket_effects_and_context_but_keeps_own(service, store, clock) -> None:
    ticket = _accept(service, store)
    store.upsert_thread(_thread("issue:o/r#2"))
    service.attach(ticket.id, thread_key="issue:o/r#2", actor="alice")
    stopped = service.stop(ticket.id, actor="alice", reason="operator stop")
    assert stopped.status is TicketStatus.CANCELLED
    kinds = {effect.kind for effect in store.due_effects(clock.now_ms())}
    assert EffectKind.CANCEL_ASSIGNMENT in kinds
    live = store.list_subscriptions(ticket_id=ticket.id, live_only=True)
    assert [item.mode for item in live] == [SubscriptionMode.OWN]
    decisions = store.list_decisions(trigger_id=_trigger().id)
    assert decisions[-1].outcome is DecisionOutcome.STOP
    assert decisions[-1].reason_codes == ["manual_stop"]


def test_stop_is_idempotent_for_a_cancelled_ticket(service, store) -> None:
    ticket = _accept(service, store)
    once = service.stop(ticket.id, actor="alice")
    twice = service.stop(ticket.id, actor="alice")
    assert once.status is TicketStatus.CANCELLED
    assert twice.status is TicketStatus.CANCELLED
    assert twice.ended_at == once.ended_at


def test_stop_on_completed_ticket_only_ends_context_subscriptions(service, store) -> None:
    ticket = _accept(service, store)
    store.upsert_thread(_thread("issue:o/r#2"))
    service.attach(ticket.id, thread_key="issue:o/r#2", actor="alice")
    store.update_ticket(ticket.model_copy(update={"status": TicketStatus.COMPLETED}))
    stopped = service.stop(ticket.id, actor="alice")
    assert stopped.status is TicketStatus.COMPLETED
    live = store.list_subscriptions(ticket_id=ticket.id, live_only=True)
    assert [item.mode for item in live] == [SubscriptionMode.OWN]


def test_handback_turns_own_into_context_and_allows_a_new_ticket(service, store) -> None:
    ticket = _accept(service, store)
    follow = _trigger(external_id="github:o/r:issue_comment:7", text="follow up")
    store.insert_trigger(follow)
    decision = service.handback(
        ticket.id, thread_key="issue:o/r#1", trigger_id=follow.id, actor="alice"
    )
    assert decision.outcome is DecisionOutcome.OBSERVE
    assert decision.reason_codes == ["handback"]
    live = {item.mode for item in store.list_subscriptions(ticket_id=ticket.id, live_only=True)}
    assert live == {SubscriptionMode.CONTEXT}
    reception = service.receive(
        _trigger(
            external_id="github:o/r:issue_comment:8",
            text="new task",
        )
    )
    assert reception.ticket_id is not None
    assert reception.ticket_id != ticket.id
    assert len(store.list_tickets()) == 2


def test_resume_refuses_cancelled_and_live_tickets(service, store) -> None:
    ticket = _accept(service, store)
    trigger = _trigger(external_id="github:o/r:issue_comment:9", text="resume please")
    store.insert_trigger(trigger)
    with pytest.raises(RouterStateError):
        service.resume(ticket.id, trigger_id=trigger.id, actor="alice")
    store.update_ticket(ticket.model_copy(update={"status": TicketStatus.CANCELLED}))
    with pytest.raises(RouterStateError):
        service.resume(ticket.id, trigger_id=trigger.id, actor="alice")


def test_resume_requires_an_admitted_explicit_request(service, store) -> None:
    ticket = _accept(service, store)
    store.update_ticket(ticket.model_copy(update={"status": TicketStatus.COMPLETED}))
    plain = _trigger(external_id="github:o/r:issue_comment:10")
    store.insert_trigger(plain)
    with pytest.raises(RouterStateError):
        service.resume(ticket.id, trigger_id=plain.id, actor="alice")


def test_resume_rejects_an_expired_mandate(service, store, clock) -> None:
    ticket = _accept(service, store)
    store.update_ticket(ticket.model_copy(update={"status": TicketStatus.COMPLETED}))
    trigger = _trigger(external_id="github:o/r:issue_comment:11", text="resume please")
    store.insert_trigger(trigger)
    clock.advance(60_001)
    with pytest.raises(RouterStateError, match="revalidation_failed"):
        service.resume(ticket.id, trigger_id=trigger.id, actor="alice")


def test_resume_requeues_with_a_new_assignment_and_reuses_the_pending_input(service, store) -> None:
    ticket = _accept(service, store)
    store.update_ticket(ticket.model_copy(update={"status": TicketStatus.COMPLETED}))
    follow = _trigger(
        external_id="github:o/r:issue_comment:12",
        text="continue the work",
    )
    service.receive(follow)
    inputs_before = len(store.list_inputs(ticket.id))
    resumed = service.resume(ticket.id, trigger_id=follow.id, actor="alice")
    assert resumed.status is TicketStatus.QUEUED
    assert len(store.list_assignments(ticket.id)) == 2
    assignments = store.list_assignments(ticket.id)
    assert [item.attempt for item in assignments] == [1, 2]
    assert len(store.list_inputs(ticket.id)) == inputs_before
    decisions = store.list_decisions(trigger_id=follow.id)
    assert decisions[-1].outcome is DecisionOutcome.ACCEPT
    assert decisions[-1].reason_codes == ["resumed"]


def test_record_pull_request_adds_own_subscription_from_a_report(service, store, clock) -> None:
    ticket = _accept(service, store)
    assignment = store.list_assignments(ticket.id)[0]
    store.upsert_thread(_thread("pr:o/r#9"))
    service.attach(ticket.id, thread_key="pr:o/r#9", actor="alice")
    store.insert_report(
        Report(
            id="rp-1",
            ticket_id=ticket.id,
            assignment_id=assignment.id,
            kind=ReportKind.READY,
            summary="opened a PR",
            created_at=clock.now_ms(),
            details={"pull_number": 9},
        )
    )
    subscription = service.record_pull_request(ticket.id, report_id="rp-1", actor="alice")
    assert subscription.thread_key == "pr:o/r#9"
    assert subscription.mode is SubscriptionMode.OWN
    live = store.list_subscriptions(ticket_id=ticket.id, thread_key="pr:o/r#9", live_only=True)
    assert [item.mode for item in live] == [SubscriptionMode.OWN]
    assert store.get_thread("pr:o/r#9") is not None


def test_record_pull_request_rejects_a_report_without_a_number(service, store, clock) -> None:
    ticket = _accept(service, store)
    assignment = store.list_assignments(ticket.id)[0]
    store.insert_report(
        Report(
            id="rp-2",
            ticket_id=ticket.id,
            assignment_id=assignment.id,
            kind=ReportKind.READY,
            summary="no number",
            created_at=clock.now_ms(),
            details={},
        )
    )
    with pytest.raises(RouterStateError, match="pull_number"):
        service.record_pull_request(ticket.id, report_id="rp-2", actor="alice")


def test_record_pull_request_rejects_an_unknown_report(service, store) -> None:
    ticket = _accept(service, store)
    with pytest.raises(RouterNotFoundError, match="report"):
        service.record_pull_request(ticket.id, report_id="rp-missing", actor="alice")


def test_reevaluate_is_operator_only_and_records_a_new_decision(service, store) -> None:
    _accept(service, store)
    trigger = _trigger(external_id="github:o/r:issue_comment:20", text="again")
    store.insert_trigger(trigger)
    with pytest.raises(RouterPermissionError):
        service.reevaluate(trigger.id, actor="mallory")
    decision = service.reevaluate(trigger.id, actor="alice")
    assert service.router.config.repository == "o/r"
    assert decision.outcome is DecisionOutcome.ATTACH
    assert len(store.list_decisions(trigger_id=trigger.id)) == 1


def test_subscribe_rejects_a_trigger_without_a_thread(service, store) -> None:
    ticket = _accept(service, store)
    trigger = _trigger(external_id="github:o/r:issue_comment:21").model_copy(
        update={"thread_keys": []}
    )
    store.insert_trigger(trigger)
    with pytest.raises(RouterStateError, match="thread key"):
        service.subscribe(ticket.id, trigger_id=trigger.id, actor="alice")


def test_handback_requires_an_own_subscription(service, store) -> None:
    ticket = _accept(service, store)
    trigger = _trigger(external_id="github:o/r:issue_comment:22", text="hand back")
    store.insert_trigger(trigger)
    service.handback(ticket.id, thread_key="issue:o/r#1", trigger_id=trigger.id, actor="alice")
    with pytest.raises(RouterStateError, match="own"):
        service.handback(ticket.id, thread_key="issue:o/r#1", trigger_id=trigger.id, actor="alice")


def test_commands_reject_unknown_identifiers(service, store) -> None:
    ticket = _accept(service, store)
    with pytest.raises(RouterNotFoundError, match="ticket"):
        service.attach("tk-missing", thread_key="issue:o/r#1", actor="alice")
    with pytest.raises(RouterNotFoundError, match="thread"):
        service.attach(ticket.id, thread_key="issue:o/r#99", actor="alice")
    with pytest.raises(RouterNotFoundError, match="trigger"):
        service.subscribe(ticket.id, trigger_id="tg-missing", actor="alice")
    store.update_ticket(ticket.model_copy(update={"status": TicketStatus.COMPLETED}))
    with pytest.raises(RouterStateError, match="已结束"):
        service.attach(ticket.id, thread_key="issue:o/r#1", actor="alice")


def test_resume_inserts_a_task_input_and_restores_own_when_missing(service, store, clock) -> None:
    ticket = _accept(service, store)
    store.update_ticket(ticket.model_copy(update={"status": TicketStatus.COMPLETED}))
    for subscription in store.list_subscriptions(ticket_id=ticket.id, live_only=True):
        store.end_subscription(subscription.id, ended_at=clock.now_ms())
    trigger = _trigger(external_id="github:o/r:issue_comment:23", text="fresh resume text")
    store.insert_trigger(trigger)
    inputs_before = len(store.list_inputs(ticket.id))
    resumed = service.resume(ticket.id, trigger_id=trigger.id, actor="alice")
    assert resumed.status is TicketStatus.QUEUED
    assert len(store.list_inputs(ticket.id)) == inputs_before + 1
    own = [
        item
        for item in store.list_subscriptions(ticket_id=ticket.id, live_only=True)
        if item.mode is SubscriptionMode.OWN
    ]
    assert [item.thread_key for item in own] == ["issue:o/r#1"]


def test_stop_records_manual_stop_even_after_all_subscriptions_end(service, store, clock) -> None:
    ticket = _accept(service, store)
    for subscription in store.list_subscriptions(ticket_id=ticket.id, live_only=True):
        store.end_subscription(subscription.id, ended_at=clock.now_ms())
    stopped = service.stop(ticket.id, actor="alice")
    assert stopped.status is TicketStatus.CANCELLED
    decisions = store.list_decisions()
    assert decisions[-1].outcome is DecisionOutcome.STOP
    assert decisions[-1].reason_codes == ["manual_stop"]


class _RecordingAudit:
    def __init__(self) -> None:
        self.admissions: list[tuple[Any, str | None]] = []
        self.revalidations: list[tuple[Any, str, Any, str | None]] = []

    def admitted(self, admission: Any, *, ticket_id: str | None = None) -> None:
        self.admissions.append((admission, ticket_id))

    def revalidated(
        self, mandate: Any, when: str, decision: Any, *, ticket_id: str | None = None
    ) -> None:
        self.revalidations.append((mandate, when, decision, ticket_id))


def test_resume_audits_admission_and_launch_revalidation(make_service, store) -> None:
    service = make_service()
    ticket = _accept(service, store)
    store.update_ticket(ticket.model_copy(update={"status": TicketStatus.COMPLETED}))
    trigger = _trigger(external_id="github:o/r:issue_comment:24", text="resume audited")
    store.insert_trigger(trigger)
    audit = _RecordingAudit()
    audited = make_service(audit=audit)
    audited.resume(ticket.id, trigger_id=trigger.id, actor="alice")
    assert len(audit.admissions) == 1
    assert audit.admissions[0][0].admitted is True
    assert [item[1] for item in audit.revalidations] == ["launch"]
    assert audit.revalidations[0][3] == ticket.id


def test_stop_revokes_pending_approvals(service, store, clock) -> None:
    ticket = _accept(service, store)
    approvals = ApprovalService(store, clock=clock, ids=SequenceIds())
    approvals.submit(ticket.id, Action.COMMENT_ISSUE, "d-1", "notification:accepted")

    service.stop(ticket.id, actor="alice")

    assert approvals.for_ticket(ticket.id, status=ApprovalStatus.PENDING) == []


def test_retry_effect_only_accepts_failed_rows(service, store, clock) -> None:
    store.enqueue_effect(
        Effect(
            id="ef-failed",
            kind=EffectKind.GITHUB_COMMENT,
            idempotency_key="notify:tk:accepted",
            payload={"ticket_id": "tk-1"},
            state=EffectState.FAILED,
            attempts=3,
            due_at=1,
            last_error="boom",
        )
    )
    store.enqueue_effect(
        Effect(
            id="ef-pending",
            kind=EffectKind.GITHUB_COMMENT,
            idempotency_key="notify:tk:result",
            payload={"ticket_id": "tk-1"},
            state=EffectState.PENDING,
            due_at=1,
        )
    )
    clock.advance(10)

    updated = service.retry_effect("ef-failed", actor="alice")

    assert updated.state is EffectState.PENDING
    assert updated.attempts == 0
    assert updated.due_at == clock.now_ms()
    assert updated.last_error is None
    assert updated.idempotency_key == "notify:tk:accepted"
    with pytest.raises(RouterStateError):
        service.retry_effect("ef-pending", actor="alice")
    with pytest.raises(RouterNotFoundError):
        service.retry_effect("ef-missing", actor="alice")
    with pytest.raises(RouterPermissionError):
        service.retry_effect("ef-failed", actor="mallory")
