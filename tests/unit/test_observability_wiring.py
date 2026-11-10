"""P7 M1：Router / EffectDispatcher 接线与旁路失败语义。"""

from __future__ import annotations

from pathlib import Path

import pytest

from stableagent.control.model import Action
from stableagent.control.policy import StaticPolicy, StaticPolicyConfig
from stableagent.observability.emitter import DomainEventEmitter
from stableagent.observability.events import DomainEventKind
from stableagent.orchestration.model import Effect, EffectKind, EffectState
from stableagent.orchestration.outbox import EffectDispatcher
from stableagent.supervisor.assessor import ObserveOnlyAssessor
from stableagent.supervisor.model import (
    AutonomyMode,
    Thread,
    ThreadKind,
    ThreadStatus,
    Trigger,
    TriggerKind,
)
from stableagent.supervisor.router import RouterConfig, RouterService
from stableagent.supervisor.store import SupervisorStore
from tests.support import FixedClock, SequenceIds


def _policy(clock: FixedClock, ids: SequenceIds) -> StaticPolicy:
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


def _trigger(*, text: str = "@agent please fix") -> Trigger:
    return Trigger(
        id="tg-1",
        external_id="github:o/r:issue:1",
        kind=TriggerKind.ISSUE,
        actor="alice",
        thread_keys=["issue:o/r#1"],
        occurred_at=1,
        received_at=1,
        payload={
            "resource": {"number": 1, "user": {"login": "alice"}},
            "request": {"text": text, "mentioned": True, "url": None},
            "deleted": False,
        },
    )


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock()


@pytest.fixture
def ids() -> SequenceIds:
    return SequenceIds()


@pytest.mark.asyncio
async def test_router_receive_emits_events(
    tmp_path: Path, clock: FixedClock, ids: SequenceIds
) -> None:
    store = SupervisorStore(tmp_path / "r.db", clock=clock)
    store.upsert_thread(
        Thread(
            key="issue:o/r#1",
            source="github",
            repository="o/r",
            kind=ThreadKind.ISSUE,
            external_ref="#1",
            last_trigger_at=1,
            status=ThreadStatus.OPEN,
        )
    )
    emitter = DomainEventEmitter(store, clock=clock, ids=ids)
    router = RouterService(
        store,
        _policy(clock, ids),
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
        emitter=emitter,
    )
    reception = router.receive(_trigger())
    assert reception.duplicate is False
    kinds = {e.kind for e in store.list_domain_events()}
    assert DomainEventKind.TRIGGER_RECEIVED in kinds


@pytest.mark.asyncio
async def test_effect_dispatch_emits_success_and_failure(
    tmp_path: Path, clock: FixedClock, ids: SequenceIds
) -> None:
    store = SupervisorStore(tmp_path / "fx2.db", clock=clock)
    emitter = DomainEventEmitter(store, clock=clock, ids=ids)

    async def ok(_effect: Effect) -> None:
        return None

    async def boom(_effect: Effect) -> None:
        raise RuntimeError("nope")

    with store.transaction():
        store.enqueue_effect(
            Effect(
                id="ef-ok",
                kind=EffectKind.GITHUB_COMMENT,
                idempotency_key="k-ok",
                payload={"ticket_id": "tk-1"},
                due_at=clock.now_ms(),
            )
        )
        store.enqueue_effect(
            Effect(
                id="ef-bad",
                kind=EffectKind.GITHUB_PULL_REQUEST,
                idempotency_key="k-bad",
                payload={"ticket_id": "tk-1"},
                due_at=clock.now_ms(),
            )
        )

    dispatcher = EffectDispatcher(
        store,
        {
            EffectKind.GITHUB_COMMENT: ok,
            EffectKind.GITHUB_PULL_REQUEST: boom,
        },
        clock=clock,
        max_attempts=1,
        emitter=emitter,
    )
    await dispatcher.dispatch_due(limit=10)
    kinds = {e.kind for e in store.list_domain_events(ticket_id="tk-1")}
    assert DomainEventKind.EFFECT_ATTEMPTED in kinds
    assert DomainEventKind.EFFECT_SUCCEEDED in kinds
    assert DomainEventKind.EFFECT_FAILED in kinds
    bad = store.get_effect("ef-bad")
    assert bad is not None
    assert bad.state is EffectState.FAILED


@pytest.mark.asyncio
async def test_emit_failure_does_not_block_effect_success(
    tmp_path: Path, clock: FixedClock, ids: SequenceIds
) -> None:
    store = SupervisorStore(tmp_path / "bypass.db", clock=clock)
    emitter = DomainEventEmitter(store, clock=clock, ids=ids)

    def _boom(_event: object) -> None:
        raise RuntimeError("sink down")

    store.insert_domain_event = _boom  # type: ignore[method-assign]

    async def ok(_effect: Effect) -> None:
        return None

    with store.transaction():
        store.enqueue_effect(
            Effect(
                id="ef-1",
                kind=EffectKind.GITHUB_COMMENT,
                idempotency_key="k1",
                payload={"ticket_id": "tk-1"},
                due_at=clock.now_ms(),
            )
        )
    dispatcher = EffectDispatcher(
        store,
        {EffectKind.GITHUB_COMMENT: ok},
        clock=clock,
        emitter=emitter,
    )
    assert await dispatcher.dispatch_due() == 1
    effect = store.get_effect("ef-1")
    assert effect is not None
    assert effect.state is EffectState.SUCCEEDED
    assert emitter.metrics.get("emit_failures") >= 1
