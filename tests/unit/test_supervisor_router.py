"""WP-6.3：receive 的原子推进、扇出与三档决策。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from stableagent.control.audit import AuditWriter
from stableagent.control.model import Action
from stableagent.control.policy import StaticPolicy, StaticPolicyConfig
from stableagent.orchestration.model import EffectKind, TicketStatus
from stableagent.supervisor.assessor import ObserveOnlyAssessor
from stableagent.supervisor.model import (
    AutonomyMode,
    DecisionOutcome,
    DecisionStrength,
    SubscriptionMode,
    Thread,
    ThreadKind,
    ThreadStatus,
    Trigger,
    TriggerKind,
)
from stableagent.supervisor.router import (
    RequestSignal,
    RouterConfig,
    RouterModeError,
    RouterService,
    ensure_subscription,
)
from stableagent.supervisor.store import SupervisorStore
from tests.support import FixedClock, SequenceIds


def _thread(key: str = "issue:o/r#1", *, status: ThreadStatus = ThreadStatus.OPEN) -> Thread:
    kind = ThreadKind.PULL_REQUEST if key.startswith("pr:") else ThreadKind.ISSUE
    ref = key.rsplit("#", 1)[-1]
    return Thread(
        key=key,
        repository="o/r",
        kind=kind,
        external_ref=f"#{ref}",
        last_trigger_at=1,
        status=status,
    )


def _trigger(
    *,
    external_id: str = "github:o/r:issue:1",
    kind: TriggerKind = TriggerKind.ISSUE,
    actor: str = "alice",
    thread_keys: list[str] | None = None,
    text: str | None = None,
    mentioned: bool = True,
    request: Any = None,
) -> Trigger:
    payload: dict[str, Any] = {
        "resource": {"number": 1, "user": {"login": actor}},
        "deleted": False,
    }
    if request is not None:
        payload["request"] = request
    elif text is not None:
        payload["request"] = {"text": text, "mentioned": mentioned, "url": None}
    return Trigger(
        id=f"tg-{external_id}",
        external_id=external_id,
        kind=kind,
        actor=actor,
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
def policy(clock: FixedClock, ids: SequenceIds) -> StaticPolicy:
    return StaticPolicy(
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


def _router(
    store: SupervisorStore,
    policy: StaticPolicy,
    clock: FixedClock,
    ids: SequenceIds,
    *,
    mode: AutonomyMode = AutonomyMode.OBSERVE,
    audit: AuditWriter | None = None,
    repository: str = "o/r",
) -> RouterService:
    return RouterService(
        store,
        policy,
        ObserveOnlyAssessor(),
        RouterConfig(
            repository=repository,
            mode=mode,
            operators=frozenset({"alice"}),
            actions=("read_repository", "run_command"),
            allowed_commands=("pytest",),
        ),
        clock=clock,
        ids=ids,
        audit=audit,
    )


def _accept(
    router: RouterService,
    store: SupervisorStore,
    *,
    thread: str = "issue:o/r#1",
    text: str = "fix it",
    external_id: str = "github:o/r:issue:1",
):
    store.upsert_thread(_thread(thread))
    reception = router.receive(_trigger(external_id=external_id, thread_keys=[thread], text=text))
    assert reception.ticket_id is not None
    ticket = store.get_ticket(reception.ticket_id)
    assert ticket is not None
    return ticket


def test_router_rejects_auto_mode(store, policy, clock, ids) -> None:
    with pytest.raises(RouterModeError):
        _router(store, policy, clock, ids, mode=AutonomyMode.AUTO)


def test_request_signal_reads_only_the_normalized_request_block() -> None:
    signal = RequestSignal.from_trigger(_trigger(text="fix it"))
    assert signal is not None
    assert (signal.text, signal.mentioned, signal.stable_id) == (
        "fix it",
        True,
        "github:o/r:issue:1",
    )


def test_receive_persists_trigger_and_writes_one_observe_decision(
    store, policy, clock, ids
) -> None:
    store.upsert_thread(_thread())
    reception = _router(store, policy, clock, ids).receive(_trigger())
    assert reception.duplicate is False
    assert len(reception.decision_ids) == 1
    decision = store.get_decision(reception.decision_ids[0])
    assert decision is not None
    assert decision.outcome is DecisionOutcome.OBSERVE
    assert decision.strength is DecisionStrength.INFERRED
    assert decision.reason_codes == ["inferred_candidate"]
    assert store.get_trigger(_trigger().id) is not None


def test_receive_duplicate_returns_duplicate_without_new_side_effects(
    store, policy, clock, ids
) -> None:
    store.upsert_thread(_thread())
    router = _router(store, policy, clock, ids)
    first = router.receive(_trigger())
    second = router.receive(_trigger())
    assert first.duplicate is False
    assert second.duplicate is True
    assert second.decision_ids == [] and second.input_ids == []
    assert len(store.list_decisions(trigger_id=_trigger().id)) == 1


def test_receive_accepts_a_trigger_already_persisted_by_the_source(
    store, policy, clock, ids
) -> None:
    store.upsert_thread(_thread())
    store.insert_trigger(_trigger())
    reception = _router(store, policy, clock, ids).receive(_trigger())
    assert reception.duplicate is False
    assert len(reception.decision_ids) == 1


def test_off_mode_ignores_inferred_without_consulting_the_assessor(
    store, policy, clock, ids
) -> None:
    class Bomb:
        def assess(self, context):  # pragma: no cover - 被调用即失败
            raise AssertionError("off 模式不得调用评估器")

    store.upsert_thread(_thread())
    router = RouterService(
        store,
        policy,
        Bomb(),
        RouterConfig(repository="o/r", mode=AutonomyMode.OFF, operators=frozenset({"alice"})),
        clock=clock,
        ids=ids,
    )
    reception = router.receive(_trigger())
    decision = store.get_decision(reception.decision_ids[0])
    assert decision is not None
    assert decision.outcome is DecisionOutcome.IGNORE
    assert decision.reason_codes == ["autonomy_off"]


def test_explicit_admission_creates_seven_facts_atomically(store, policy, clock, ids) -> None:
    store.upsert_thread(_thread())
    audit = AuditWriter(policy, store, clock=clock, ids=ids)
    router = _router(store, policy, clock, ids, audit=audit)
    reception = router.receive(_trigger(text="fix the bug"))
    assert reception.ticket_id is not None
    ticket = store.get_ticket(reception.ticket_id)
    assert ticket is not None and ticket.status is TicketStatus.QUEUED
    mandate = store.get_mandate(ticket.mandate_id)
    assert mandate is not None
    assert mandate.origin_kind == "github"
    assert mandate.requester == "alice"
    assert mandate.scope == "github:o/r:issue:1"
    assert mandate.actions == ["read_repository", "run_command"]
    assert [(item.kind, item.payload) for item in store.list_inputs(ticket.id)] == [
        ("task", "fix the bug")
    ]
    assert len(store.list_assignments(ticket.id)) == 1
    assert [effect.kind for effect in store.due_effects(clock.now_ms())] == [
        EffectKind.LAUNCH_ASSIGNMENT
    ]
    assert [
        subscription.mode
        for subscription in store.list_subscriptions(ticket_id=ticket.id, live_only=True)
    ] == [SubscriptionMode.OWN]
    decision = store.get_decision(reception.decision_ids[0])
    assert decision is not None
    assert decision.outcome is DecisionOutcome.ACCEPT
    assert decision.strength is DecisionStrength.EXPLICIT
    assert decision.mandate_id == mandate.id
    assert decision.reason_codes == ["admitted"]
    assert len(store.list_audit(kind="admit")) == 1


def test_explicit_rejection_records_not_admitted_ignore(store, policy, clock, ids) -> None:
    store.upsert_thread(_thread())
    router = _router(store, policy, clock, ids)
    reception = router.receive(_trigger(actor="mallory", text="fix it"))
    decision = store.get_decision(reception.decision_ids[0])
    assert decision is not None
    assert decision.outcome is DecisionOutcome.IGNORE
    assert decision.strength is DecisionStrength.NOT_ADMITTED
    assert decision.reason_codes == ["actor_not_allowlisted"]
    assert store.list_tickets() == [] and reception.ticket_id is None


def test_explicit_without_a_mention_is_ignored(store, policy, clock, ids) -> None:
    store.upsert_thread(_thread())
    reception = _router(store, policy, clock, ids).receive(_trigger(text="fix it", mentioned=False))
    decision = store.get_decision(reception.decision_ids[0])
    assert decision is not None
    assert decision.reason_codes == ["mention_required"]


def test_router_and_policy_repository_mismatch_is_ignored(store, policy, clock, ids) -> None:
    store.upsert_thread(_thread())
    router = _router(store, policy, clock, ids, repository="other/r")
    reception = router.receive(_trigger(text="fix it"))
    decision = store.get_decision(reception.decision_ids[0])
    assert decision is not None
    assert decision.reason_codes == ["repository_mismatch"]
    assert store.list_tickets() == []


def test_malformed_request_block_is_ignored(store, policy, clock, ids) -> None:
    store.upsert_thread(_thread())
    reception = _router(store, policy, clock, ids).receive(
        _trigger(request={"text": 1, "mentioned": True})
    )
    decision = store.get_decision(reception.decision_ids[0])
    assert decision is not None
    assert decision.outcome is DecisionOutcome.IGNORE
    assert decision.strength is DecisionStrength.NOT_ADMITTED
    assert decision.reason_codes == ["malformed_request"]


def test_explicit_without_a_thread_row_is_ignored(store, policy, clock, ids) -> None:
    reception = _router(store, policy, clock, ids).receive(_trigger(text="fix it"))
    decision = store.get_decision(reception.decision_ids[0])
    assert decision is not None
    assert decision.reason_codes == ["thread_unknown"]
    assert store.list_tickets() == []


def test_followup_on_an_owned_thread_attaches_without_a_new_ticket(
    store, policy, clock, ids
) -> None:
    router = _router(store, policy, clock, ids)
    ticket = _accept(router, store)
    reception = router.receive(
        _trigger(
            external_id="github:o/r:issue_comment:2",
            kind=TriggerKind.ISSUE_COMMENT,
            text="also add tests",
        )
    )
    decision = store.get_decision(reception.decision_ids[0])
    assert decision is not None
    assert decision.outcome is DecisionOutcome.ATTACH
    assert decision.strength is DecisionStrength.EXPLICIT
    assert decision.reason_codes == ["owned_thread_followup"]
    assert reception.ticket_id == ticket.id
    assert len(store.list_tickets()) == 1
    assert len(store.list_inputs(ticket.id)) == 2


def test_duplicate_request_digest_attaches_to_the_existing_ticket(
    store, policy, clock, ids
) -> None:
    router = _router(store, policy, clock, ids)
    ticket = _accept(router, store, text="fix it")
    store.upsert_thread(_thread("issue:o/r#2"))
    reception = router.receive(
        _trigger(
            external_id="github:o/r:issue:2",
            thread_keys=["issue:o/r#2"],
            text="fix it",
        )
    )
    decision = store.get_decision(reception.decision_ids[0])
    assert decision is not None
    assert decision.outcome is DecisionOutcome.ATTACH
    assert decision.reason_codes == ["duplicate_request"]
    assert decision.attempt == 2
    assert reception.ticket_id == ticket.id
    assert len(store.list_tickets()) == 1
    subscriptions = {
        (item.thread_key, item.mode)
        for item in store.list_subscriptions(ticket_id=ticket.id, live_only=True)
    }
    assert ("issue:o/r#2", SubscriptionMode.CONTEXT) in subscriptions


def test_followup_on_a_stopped_ticket_is_a_stop_decision(store, policy, clock, ids) -> None:
    router = _router(store, policy, clock, ids)
    ticket = _accept(router, store)
    store.update_ticket(ticket.model_copy(update={"status": TicketStatus.CANCELLED}))
    before = len(store.list_inputs(ticket.id))
    reception = router.receive(
        _trigger(
            external_id="github:o/r:issue_comment:3",
            kind=TriggerKind.ISSUE_COMMENT,
            text="please continue",
        )
    )
    decision = store.get_decision(reception.decision_ids[0])
    assert decision is not None
    assert decision.outcome is DecisionOutcome.STOP
    assert decision.reason_codes == ["stopped_ignored"]
    assert reception.ticket_id == ticket.id
    assert len(store.list_inputs(ticket.id)) == before


def test_followup_on_a_completed_ticket_keeps_the_input_and_observes(
    store, policy, clock, ids
) -> None:
    router = _router(store, policy, clock, ids)
    ticket = _accept(router, store)
    store.update_ticket(ticket.model_copy(update={"status": TicketStatus.COMPLETED}))
    reception = router.receive(
        _trigger(
            external_id="github:o/r:issue_comment:4",
            kind=TriggerKind.ISSUE_COMMENT,
            text="one more thing",
        )
    )
    decision = store.get_decision(reception.decision_ids[0])
    assert decision is not None
    assert decision.outcome is DecisionOutcome.OBSERVE
    assert decision.reason_codes == ["resume_requires_operator"]
    assert len(store.list_inputs(ticket.id)) == 2
    assert EffectKind.DELIVER_INPUT not in {
        effect.kind for effect in store.due_effects(clock.now_ms())
    }


def test_multiple_owners_observe_ambiguous_without_a_new_ticket(store, policy, clock, ids) -> None:
    router = _router(store, policy, clock, ids)
    first = _accept(router, store, thread="issue:o/r#1", text="first task")
    second = _accept(
        router,
        store,
        thread="issue:o/r#2",
        text="second task",
        external_id="github:o/r:issue:2",
    )
    ensure_subscription(
        store,
        ids=ids,
        ticket_id=second.id,
        thread_key="issue:o/r#1",
        mode=SubscriptionMode.OWN,
        created_at=clock.now_ms(),
    )
    reception = router.receive(_trigger(external_id="github:o/r:issue:3", text="third task"))
    decision = store.get_decision(reception.decision_ids[0])
    assert decision is not None
    assert decision.outcome is DecisionOutcome.OBSERVE
    assert decision.reason_codes == ["ambiguous_owner"]
    assert len(store.list_tickets()) == 2
    assert first.id != second.id


def test_fanout_delivers_one_input_per_ticket_across_thread_keys(store, policy, clock, ids) -> None:
    router = _router(store, policy, clock, ids)
    first = _accept(router, store, thread="issue:o/r#1", text="first task")
    second = _accept(
        router,
        store,
        thread="issue:o/r#2",
        text="second task",
        external_id="github:o/r:issue:2",
    )
    ensure_subscription(
        store,
        ids=ids,
        ticket_id=second.id,
        thread_key="issue:o/r#1",
        mode=SubscriptionMode.CONTEXT,
        created_at=clock.now_ms(),
    )
    reception = router.receive(
        _trigger(
            external_id="github:o/r:issue:3",
            thread_keys=["issue:o/r#1", "issue:o/r#2"],
        )
    )
    assert reception.fanned_out == 2
    assert len(store.list_inputs(first.id)) == 2
    assert len(store.list_inputs(second.id)) == 2
    deliver = [
        effect
        for effect in store.due_effects(clock.now_ms())
        if effect.kind is EffectKind.DELIVER_INPUT
    ]
    assert len(deliver) == 2


def test_terminal_subscriptions_are_not_fanned_out(store, policy, clock, ids) -> None:
    router = _router(store, policy, clock, ids)
    ticket = _accept(router, store)
    store.update_ticket(ticket.model_copy(update={"status": TicketStatus.COMPLETED}))
    before = len(store.list_inputs(ticket.id))
    receipt = router.receive(
        _trigger(external_id="github:o/r:issue_comment:5", kind=TriggerKind.ISSUE_COMMENT)
    )
    assert receipt.fanned_out == 0
    assert len(store.list_inputs(ticket.id)) == before


def test_context_subscription_never_expands_the_mandate(store, policy, clock, ids) -> None:
    router = _router(store, policy, clock, ids)
    ticket = _accept(router, store, thread="issue:o/r#1", text="first task")
    ensure_subscription(
        store,
        ids=ids,
        ticket_id=ticket.id,
        thread_key="issue:o/r#2",
        mode=SubscriptionMode.CONTEXT,
        created_at=clock.now_ms(),
    )
    mandate_before = store.get_mandate(ticket.mandate_id)
    router.receive(_trigger(external_id="github:o/r:issue_comment:6", thread_keys=["issue:o/r#2"]))
    mandate_after = store.get_mandate(ticket.mandate_id)
    assert mandate_before is not None and mandate_after is not None
    assert mandate_after.actions == mandate_before.actions
    assert len(store.list_tickets()) == 1
    assert len(store.list_inputs(ticket.id)) == 2
