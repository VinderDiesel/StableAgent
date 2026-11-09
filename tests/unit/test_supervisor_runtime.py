"""WP-6.4：单实例锁、启动恢复与每轮 poll/route/tick 推进。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from stableagent.control.model import Action
from stableagent.control.policy import StaticPolicy, StaticPolicyConfig
from stableagent.orchestration.model import EffectKind
from stableagent.supervisor.model import Thread, ThreadKind, Trigger, TriggerKind
from stableagent.supervisor.router import RouterConfig
from stableagent.supervisor.runtime import (
    RuntimeConfig,
    RuntimeLockError,
    build_runtime,
)
from stableagent.supervisor.source import PollBatch, SourceEvent, SourceTransientError
from stableagent.supervisor.store import SupervisorStore
from tests.support import FixedClock, SequenceIds


def _event(number: int = 1) -> SourceEvent:
    key = f"issue:o/r#{number}"
    thread = Thread(
        key=key,
        repository="o/r",
        kind=ThreadKind.ISSUE,
        external_ref=f"#{number}",
        last_trigger_at=1,
    )
    trigger = Trigger(
        id=f"tg-{number}",
        external_id=f"github:o/r:issue:{number}",
        kind=TriggerKind.ISSUE,
        actor="alice",
        thread_keys=[key],
        occurred_at=1,
        received_at=1,
        payload={
            "resource": {"number": number, "user": {"login": "alice"}},
            "deleted": False,
            "request": {"text": f"task {number}", "mentioned": True, "url": None},
        },
    )
    return SourceEvent(trigger=trigger, thread=thread)


class ScriptedSource:
    def __init__(self, store: SupervisorStore) -> None:
        self._store = store
        self.events: list[SourceEvent] = []
        self.fail_next: Exception | None = None
        self.calls = 0

    def feed(self, event: SourceEvent) -> None:
        self.events.append(event)

    async def poll(self, *, limit: int = 100) -> PollBatch:
        self.calls += 1
        failure, self.fail_next = self.fail_next, None
        if failure is not None:
            raise failure
        batch, self.events = self.events[:limit], self.events[limit:]
        with self._store.transaction():
            for event in batch:
                self._store.insert_trigger(event.trigger)
                self._store.upsert_thread(event.thread)
                if event.channel is not None:
                    self._store.insert_channel(event.channel)
        return PollBatch(events=batch)


class RecordingExecutor:
    def __init__(self) -> None:
        self.launched: list[str] = []
        self.recovered: list[str] = []
        self.delivered: list[tuple[str, str]] = []
        self.cancelled: list[str] = []
        self.gate = asyncio.Event()
        self.gate.set()

    async def launch(self, assignment, ticket, inputs) -> None:
        self.launched.append(assignment.id)
        await self.gate.wait()

    async def deliver(self, assignment, ticket, inputs) -> None:
        self.delivered.extend((ticket.id, item.payload) for item in inputs)

    async def cancel(self, assignment) -> None:
        self.cancelled.append(assignment.id)

    async def recover(self, assignment, ticket) -> None:
        self.recovered.append(assignment.id)


async def _noop(effect) -> None:
    return None


@pytest.fixture
def env(tmp_path: Path):
    clock, ids = FixedClock(), SequenceIds()
    store = SupervisorStore(tmp_path / "runtime.db", clock=clock)
    policy = StaticPolicy(
        StaticPolicyConfig(
            repository="o/r",
            actors=frozenset({"alice"}),
            grantable_actions=frozenset({Action.READ_REPOSITORY}),
            default_validity_ms=60_000,
        ),
        clock=clock,
        ids=ids,
    )
    executor = RecordingExecutor()
    source = ScriptedSource(store)
    bundle = build_runtime(
        store=store,
        policy=policy,
        source=source,
        executor=executor,
        effect_handlers={EffectKind.GITHUB_COMMENT: _noop},
        config=RuntimeConfig(
            router=RouterConfig(
                repository="o/r",
                operators=frozenset({"alice"}),
                actions=("read_repository",),
            ),
            owner="test-runtime",
            lock_ttl_ms=1_000,
            stop_grace_ms=10,
            step_interval_ms=0,
        ),
        clock=clock,
        ids=ids,
    )
    try:
        yield bundle, store, clock, source, executor
    finally:
        store.close()


async def test_start_routes_persisted_triggers_and_acquires_lock(env) -> None:
    bundle, store, _, _, _ = env
    event = _event()
    with store.transaction():
        store.insert_trigger(event.trigger)
        store.upsert_thread(event.thread)

    started = bundle.runtime.start()

    assert started.routed == 1 and started.paused is False
    lock = store.get_runtime_lock()
    assert lock is not None and lock.owner == "test-runtime"
    assert store.list_unrouted_triggers() == []
    assert len(store.list_decisions()) == 1


async def test_start_refuses_second_owner_without_recovery(env) -> None:
    bundle, store, clock, _, _ = env
    assert store.acquire_runtime_lock("other", now=clock.now_ms(), ttl_ms=60_000) is True

    with pytest.raises(RuntimeLockError):
        bundle.runtime.start()

    assert store.get_runtime_lock().owner == "other"  # 没有抢占，也没有写恢复记录


async def test_step_polls_routes_and_dispatches_launch(env) -> None:
    bundle, store, _, source, executor = env
    source.feed(_event())
    bundle.runtime.start()

    step = await bundle.runtime.step()
    await asyncio.sleep(0)  # create_task 的 launch 在下一轮事件循环记录

    assert (step.received, step.routed, step.dispatched) == (1, 1, 1)
    assert len(store.list_tickets()) == 1
    assert len(executor.launched) == 1
    assert store.get_ticket(store.list_tickets()[0].id).status.value == "running"


async def test_step_while_paused_records_triggers_without_routing(env) -> None:
    bundle, store, clock, source, _ = env
    store.set_runtime_paused(True, actor="alice", now=clock.now_ms(), reason="maintenance")
    bundle.runtime.start()
    source.feed(_event())

    step = await bundle.runtime.step()

    assert step.paused is True and step.received == 1 and step.routed == 0
    assert step.dispatched == 0
    assert store.list_decisions() == []
    assert len(store.list_unrouted_triggers()) == 1


async def test_step_fails_when_lease_is_lost(env) -> None:
    bundle, store, clock, _, _ = env
    bundle.runtime.start()
    clock.advance(1_001)  # 租约过期后另一进程接管
    assert store.acquire_runtime_lock("other", now=clock.now_ms(), ttl_ms=60_000) is True

    with pytest.raises(RuntimeLockError):
        await bundle.runtime.step()


async def test_source_error_does_not_stop_effect_dispatch(env) -> None:
    bundle, _, clock, source, _ = env
    bundle.orchestration.queue_ticket(bundle.orchestration.create_ticket("排队").id)
    source.fail_next = SourceTransientError("503")
    bundle.runtime.start()

    step = await bundle.runtime.step()

    assert step.source_error == "source.transient"
    assert step.dispatched == 1  # Source 失败不阻断已有 effect


async def test_status_reports_runtime_facts_without_start(env) -> None:
    bundle, _, _, _, _ = env
    offline = bundle.runtime.status()
    assert offline.started is False and offline.owner is None
    assert offline.paused is False

    bundle.runtime.start()
    online = bundle.runtime.status()
    assert online.started is True and online.owner == "test-runtime"
    assert online.queued_tickets == 0 and online.source_health == ()
