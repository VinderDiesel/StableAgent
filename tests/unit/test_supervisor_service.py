"""WP-6.3：命令层权限、context 订阅与人工 stop。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from stableagent.control.model import Action
from stableagent.control.policy import StaticPolicy, StaticPolicyConfig
from stableagent.orchestration.model import EffectKind, TicketStatus
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
