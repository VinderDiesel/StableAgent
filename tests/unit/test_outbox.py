"""C1/C3 + L6：提交后才执行、重复 dispatch 不新增副作用、断言不持有事务（C5 后半与 C4 归 M2）。"""

from __future__ import annotations

import pytest

from stableagent.orchestration.errors import ReentrantTransactionError
from stableagent.orchestration.model import Effect, EffectKind, EffectState
from stableagent.orchestration.outbox import EffectDispatcher
from stableagent.orchestration.store import Store
from tests.support import FixedClock


def _launch(
    effect_id: str, key: str, *, due: int = 0, state: EffectState = EffectState.PENDING
) -> Effect:
    return Effect(
        id=effect_id,
        kind=EffectKind.LAUNCH_ASSIGNMENT,
        idempotency_key=key,
        payload={"assignment_id": "as-1"},
        state=state,
        due_at=due,
    )


@pytest.fixture
def store(tmp_path):
    handle = Store(tmp_path / "outbox.db", clock=FixedClock())
    yield handle
    handle.close()


def test_enqueue_is_idempotent_on_kind_and_key(store: Store) -> None:
    with store.transaction():
        first = store.enqueue_effect(_launch("ef-1", "launch:as-1:1"))
        duplicate = store.enqueue_effect(_launch("ef-2", "launch:as-1:1", due=9))
    assert first.id == duplicate.id == "ef-1"  # 不新增行
    assert duplicate.payload == {"assignment_id": "as-1"}
    assert store._conn.execute("SELECT COUNT(*) FROM effects").fetchone()[0] == 1


def test_duplicate_enqueue_on_terminal_effect_does_not_revive_it(store: Store) -> None:
    with store.transaction():
        store.enqueue_effect(_launch("ef-1", "k1"))
    store.update_effect(
        Effect.model_validate(
            {**store._effect_row("ef-1").model_dump(), "state": EffectState.SUCCEEDED}
        )
    )
    with store.transaction():
        again = store.enqueue_effect(_launch("ef-2", "k1"))
    assert again.state is EffectState.SUCCEEDED and again.id == "ef-1"


async def test_handler_never_sees_an_open_transaction(store: Store) -> None:
    seen: list[bool] = []

    async def handler(effect: Effect) -> None:
        seen.append(store.in_transaction)

    with store.transaction():
        store.enqueue_effect(_launch("ef-1", "k1"))
    dispatcher = EffectDispatcher(
        store, {EffectKind.LAUNCH_ASSIGNMENT: handler}, clock=FixedClock()
    )
    assert await dispatcher.dispatch_due() == 1
    assert seen == [False]
    assert dispatcher.outcomes() == [("ef-1", EffectState.SUCCEEDED)]


async def test_handler_exception_retries_then_fails_without_memory_flags(store: Store) -> None:
    clock = FixedClock()

    async def boom(effect: Effect) -> None:
        raise RuntimeError("provider down")

    with store.transaction():
        store.enqueue_effect(_launch("ef-1", "k1"))
    dispatcher = EffectDispatcher(
        store, {EffectKind.LAUNCH_ASSIGNMENT: boom}, clock=clock, max_attempts=2
    )
    assert await dispatcher.dispatch_due() == 0
    state = store._effect_row("ef-1")
    assert state.state is EffectState.RETRY_WAIT and state.attempts == 1
    clock.advance(10_000)  # 越过 backoff 的 due_at
    assert await dispatcher.dispatch_due() == 0
    assert store._effect_row("ef-1").state is EffectState.FAILED


async def test_orphan_running_effects_are_reclaimed(store: Store) -> None:
    with store.transaction():
        store.enqueue_effect(_launch("ef-1", "k1", state=EffectState.RUNNING))
    assert store.reclaim_running_effects(5) == 1
    assert store._effect_row("ef-1").state is EffectState.PENDING


def test_transaction_wrapper_still_rejects_nesting(store: Store) -> None:
    with pytest.raises(ReentrantTransactionError), store.transaction(), store.transaction():
        pass


async def test_ineligible_effects_are_skipped_without_claim(store: Store) -> None:
    async def handler(effect: Effect) -> None:
        raise AssertionError("门控关闭时不该被调用")

    with store.transaction():
        store.enqueue_effect(_launch("ef-1", "k1"))
    dispatcher = EffectDispatcher(
        store,
        {EffectKind.LAUNCH_ASSIGNMENT: handler},
        clock=FixedClock(),
        eligible={EffectKind.LAUNCH_ASSIGNMENT: lambda e: False},
    )
    assert await dispatcher.dispatch_due() == 0
    row = store._effect_row("ef-1")
    assert (row.state, row.attempts) == (EffectState.PENDING, 0)  # 不领取、不记 attempts


async def test_deferrable_error_backs_off_without_burning_budget(store: Store) -> None:
    from stableagent.orchestration.errors import DeferrableEffectError

    async def defer(effect: Effect) -> None:
        raise DeferrableEffectError("park lost")

    with store.transaction():
        store.enqueue_effect(_launch("ef-1", "k1"))
    dispatcher = EffectDispatcher(
        store, {EffectKind.LAUNCH_ASSIGNMENT: defer}, clock=FixedClock(), max_attempts=1
    )
    for _ in range(3):  # 退避三轮也不判死
        assert await dispatcher.dispatch_due() == 0
        row = store._effect_row("ef-1")
        assert (row.state, row.attempts) == (EffectState.RETRY_WAIT, 0)
        # 时钟由 FixedClock 固定、due_at=now+backoff：后两轮在 due 集合外，此断言只锁第一轮形状


async def test_backoff_fn_is_injected_and_exhaustion_fires_hook(store: Store) -> None:
    seen: list[int] = []
    exhausted: list[tuple[str, str]] = []

    def backoff(attempts: int) -> int:
        seen.append(attempts)
        return 7 * attempts

    async def boom(effect: Effect) -> None:
        raise RuntimeError("provider down")

    clock = FixedClock()
    with store.transaction():
        store.enqueue_effect(_launch("ef-1", "k1"))
    dispatcher = EffectDispatcher(
        store,
        {EffectKind.LAUNCH_ASSIGNMENT: boom},
        clock=clock,
        max_attempts=2,
        backoff=backoff,
        on_exhausted=lambda e, d: exhausted.append((e.id, d)),
    )
    assert await dispatcher.dispatch_due() == 0
    row = store._effect_row("ef-1")
    assert (row.state, row.due_at - clock.now_ms()) == (EffectState.RETRY_WAIT, 7)
    clock.advance(100)
    assert await dispatcher.dispatch_due() == 0
    assert store._effect_row("ef-1").state is EffectState.FAILED
    assert seen == [1, 2] and len(exhausted) == 1 and exhausted[0][0] == "ef-1"
