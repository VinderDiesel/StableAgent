"""WP-6.3：命令层权限、context 订阅与人工 stop。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from stableagent.control.model import Action
from stableagent.control.policy import StaticPolicy, StaticPolicyConfig
from stableagent.orchestration.model import EffectKind, Report, ReportKind, TicketStatus
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
def service(store: SupervisorStore, clock: FixedClock, ids: SequenceIds) -> SupervisorService:
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
    return SupervisorService(store, policy, router, clock=clock, ids=ids)


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
    follow = _trigger(
        external_id="github:o/r:issue_comment:7", text="follow up"
    )
    store.insert_trigger(follow)
    decision = service.handback(
        ticket.id, thread_key="issue:o/r#1", trigger_id=follow.id, actor="alice"
    )
    assert decision.outcome is DecisionOutcome.OBSERVE
    assert decision.reason_codes == ["handback"]
    live = {
        item.mode
        for item in store.list_subscriptions(ticket_id=ticket.id, live_only=True)
    }
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


def test_resume_requeues_with_a_new_assignment_and_reuses_the_pending_input(
    service, store
) -> None:
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
    subscription = service.record_pull_request(
        ticket.id, report_id="rp-1", actor="alice"
    )
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
