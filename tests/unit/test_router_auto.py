"""P7 M6：Router AUTO 开/关/回滚与 inferred ACCEPT。"""

from __future__ import annotations

from pathlib import Path

import pytest

from stableagent.control.model import Action
from stableagent.control.policy import StaticPolicy, StaticPolicyConfig
from stableagent.supervisor.assessor import ObserveOnlyAssessor
from stableagent.supervisor.auto import AutoGate, AutoRule
from stableagent.supervisor.model import (
    AutonomyMode,
    DecisionOutcome,
    DecisionStrength,
    Thread,
    ThreadKind,
    ThreadStatus,
    Trigger,
    TriggerKind,
)
from stableagent.supervisor.router import RouterConfig, RouterService
from stableagent.supervisor.store import SupervisorStore
from tests.support import FixedClock, SequenceIds


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(1_000)


@pytest.fixture
def ids() -> SequenceIds:
    return SequenceIds()


@pytest.fixture
def store(tmp_path: Path, clock: FixedClock) -> SupervisorStore:
    return SupervisorStore(tmp_path / "auto.db", clock=clock)


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


def _thread() -> Thread:
    return Thread(
        key="issue:o/r#1",
        repository="o/r",
        kind=ThreadKind.ISSUE,
        external_ref="#1",
        last_trigger_at=1,
        status=ThreadStatus.OPEN,
    )


def _trigger(*, external_id: str = "github:o/r:issue:9") -> Trigger:
    return Trigger(
        id=f"tg-{external_id}",
        external_id=external_id,
        kind=TriggerKind.ISSUE,
        actor="alice",
        thread_keys=["issue:o/r#1"],
        occurred_at=1,
        received_at=1,
        payload={"resource": {"number": 9}},
        tenant_id="default",
    )


def _router(
    store: SupervisorStore,
    policy: StaticPolicy,
    clock: FixedClock,
    ids: SequenceIds,
    gate: AutoGate,
) -> RouterService:
    return RouterService(
        store,
        policy,
        ObserveOnlyAssessor(),
        RouterConfig(
            repository="o/r",
            mode=AutonomyMode.AUTO,
            operators=frozenset({"alice"}),
            actions=("read_repository", "run_command"),
            allowed_commands=("pytest",),
        ),
        clock=clock,
        ids=ids,
        auto_gate=gate,
    )


def test_enable_accepts_disable_rolls_back(
    store: SupervisorStore,
    policy: StaticPolicy,
    clock: FixedClock,
    ids: SequenceIds,
) -> None:
    store.upsert_thread(_thread())
    gate = AutoGate()
    gate.register(
        AutoRule(id="r1", tenant_id="default", repository="o/r", enabled=False),
        operator="admin",
    )
    router = _router(store, policy, clock, ids, gate)

    observed = router.receive(_trigger(external_id="github:o/r:issue:10"))
    d0 = store.get_decision(observed.decision_ids[0])
    assert d0 is not None and d0.outcome is DecisionOutcome.OBSERVE

    gate.enable("r1", operator="admin")
    accepted = router.receive(_trigger(external_id="github:o/r:issue:11"))
    d1 = store.get_decision(accepted.decision_ids[0])
    assert d1 is not None
    assert d1.outcome is DecisionOutcome.ACCEPT
    assert d1.strength is DecisionStrength.INFERRED
    assert "auto_accepted" in d1.reason_codes
    assert accepted.ticket_id is not None

    gate.disable("r1", operator="admin")
    rolled = router.receive(_trigger(external_id="github:o/r:issue:12"))
    d2 = store.get_decision(rolled.decision_ids[0])
    assert d2 is not None and d2.outcome is DecisionOutcome.OBSERVE
    assert rolled.ticket_id is None
