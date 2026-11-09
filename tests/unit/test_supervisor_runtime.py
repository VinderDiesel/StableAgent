"""WP-6.4：单实例锁、启动恢复与每轮 poll/route/tick 推进。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from stableagent.control.model import Action
from stableagent.control.policy import StaticPolicy, StaticPolicyConfig
from stableagent.orchestration.model import EffectKind, TicketAction, TicketStatus
from stableagent.supervisor.model import (
    DecisionOutcome,
    Thread,
    ThreadKind,
    Trigger,
    TriggerKind,
)
from stableagent.supervisor.notifications import NotificationConfig
from stableagent.supervisor.router import (
    RouterConfig,
    RouterPermissionError,
    RouterStateError,
)
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
async def env(tmp_path: Path):
    clock, ids = FixedClock(), SequenceIds()
    store = SupervisorStore(tmp_path / "runtime.db", clock=clock)
    policy = StaticPolicy(
        StaticPolicyConfig(
            repository="o/r",
            actors=frozenset({"alice"}),
            grantable_actions=frozenset({Action.READ_REPOSITORY, Action.COMMENT_ISSUE}),
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
                actions=("read_repository", "comment_issue"),
            ),
            owner="test-runtime",
            lock_ttl_ms=1_000,
            stop_grace_ms=10,
            step_interval_ms=0,
            notifications=NotificationConfig(enabled=True, max_chars=400, since_ms=0),
        ),
        clock=clock,
        ids=ids,
    )
    try:
        yield bundle, store, clock, source, executor
    finally:
        executor.gate.set()
        await bundle.runtime.stop()
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


async def test_pause_persists_and_resume_routes_pending_triggers(env) -> None:
    bundle, store, _, source, _ = env
    bundle.runtime.start()
    control = bundle.controller.pause_processing(actor="alice", reason="maintenance")
    assert control.paused is True
    assert store.get_runtime_control().reason == "maintenance"
    source.feed(_event())

    paused_step = await bundle.runtime.step()
    assert paused_step.received == 1 and store.list_decisions() == []

    resumed = bundle.controller.resume_processing(actor="alice")
    assert resumed.paused is False
    assert len(store.list_decisions()) == 1  # 暂停期间记录的 Trigger 立即补路由


async def test_pause_control_is_visible_in_status(env) -> None:
    bundle, _, _, _, _ = env
    bundle.controller.pause_processing(actor="alice", reason="maintenance")

    status = bundle.controller.status()

    assert status.paused is True
    assert status.paused_by == "alice" and status.reason == "maintenance"
    assert status.paused_at is not None


async def test_control_commands_are_operator_gated_case_insensitively(env) -> None:
    bundle, _, _, _, _ = env
    with pytest.raises(RouterPermissionError):
        bundle.controller.pause_processing(actor="mallory")
    control = bundle.controller.pause_processing(actor="ALICE")
    assert control.paused is True and control.actor == "ALICE"
    with pytest.raises(RouterPermissionError):
        bundle.controller.resume_processing(actor="mallory")


async def test_stop_waits_for_the_worker_without_cancelling_it(env) -> None:
    bundle, store, _, source, executor = env
    executor.gate.clear()
    source.feed(_event())
    bundle.runtime.start()
    await bundle.runtime.step()
    await asyncio.sleep(0)
    assert executor.launched

    stopping = asyncio.create_task(bundle.runtime.stop())
    await asyncio.sleep(0)
    assert stopping.done() is False  # 宽限期内不硬杀
    assert executor.cancelled == []

    executor.gate.set()
    result = await stopping
    assert result.drained is True and result.running_assignments == 0
    assert store.get_runtime_lock() is None


async def test_stop_is_idempotent_and_releases_the_lock(env) -> None:
    bundle, store, _, _, _ = env
    bundle.runtime.start()

    first = await bundle.runtime.stop()
    second = await bundle.runtime.stop()

    assert first.drained is True and second.drained is True
    assert store.get_runtime_lock() is None


async def test_serve_runs_bounded_steps_then_stops(env) -> None:
    bundle, store, _, source, executor = env
    source.feed(_event())
    bundle.runtime.start()

    steps = await bundle.runtime.serve(max_steps=2)

    assert steps == 2
    assert len(executor.launched) == 1
    assert store.get_runtime_lock() is None


async def test_retry_ticket_only_accepts_failed_and_is_operator_gated(env) -> None:
    bundle, store, _, source, _ = env
    source.feed(_event())
    bundle.runtime.start()
    await bundle.runtime.step()
    ticket = store.list_tickets()[0]

    with pytest.raises(RouterStateError):
        bundle.controller.retry_ticket(ticket.id, actor="alice")
    with pytest.raises(RouterPermissionError):
        bundle.controller.retry_ticket(ticket.id, actor="mallory")

    bundle.orchestration.transition_ticket(ticket.id, TicketAction.FAIL)
    assignment = bundle.controller.retry_ticket(ticket.id, actor="alice")
    assert assignment.attempt == 2
    assert store.get_ticket(ticket.id).status is TicketStatus.QUEUED


async def test_stop_ticket_cancels_and_records_a_stop_decision(env) -> None:
    bundle, store, _, source, _ = env
    source.feed(_event())
    bundle.runtime.start()
    await bundle.runtime.step()
    ticket = store.list_tickets()[0]

    stopped = bundle.controller.stop_ticket(ticket.id, actor="alice", reason="duplicate")

    assert stopped.status is TicketStatus.CANCELLED
    assert DecisionOutcome.STOP in [decision.outcome for decision in store.list_decisions()]


async def test_reevaluate_appends_an_attempt_without_new_triggers(env) -> None:
    bundle, store, _, source, _ = env
    source.feed(_event())
    bundle.runtime.start()
    await bundle.runtime.step()
    trigger = store.list_triggers()[0]
    before = len(store.list_triggers())

    decision = bundle.controller.reevaluate(trigger.id, actor="alice")

    assert decision.attempt == 2
    assert len(store.list_triggers()) == before


async def test_step_drafts_one_notification_approval(env) -> None:
    bundle, store, _, source, _ = env
    source.feed(_event())
    bundle.runtime.start()

    step = await bundle.runtime.step()

    assert step.drafted == 1
    pending = store.list_pending_approvals()
    assert [(row["ticket_id"], row["reason"]) for row in pending] == [
        (store.list_tickets()[0].id, "notification:accepted")
    ]


async def test_paused_step_does_not_draft(env) -> None:
    bundle, store, clock, source, _ = env
    store.set_runtime_paused(True, actor="alice", now=clock.now_ms(), reason="maint")
    bundle.runtime.start()
    source.feed(_event())

    step = await bundle.runtime.step()

    assert step.paused is True and step.drafted == 0
    assert store.list_pending_approvals() == []


async def test_external_resume_routes_pending_then_drafts(env) -> None:
    bundle, store, clock, source, _ = env
    store.set_runtime_paused(True, actor="alice", now=clock.now_ms(), reason="maint")
    bundle.runtime.start()
    source.feed(_event())
    assert (await bundle.runtime.step()).drafted == 0
    store.set_runtime_paused(False, actor="alice", now=clock.now_ms())

    step = await bundle.runtime.step()

    assert (step.paused, step.routed, step.drafted) == (False, 1, 1)
    assert len(store.list_tickets()) == 1


async def test_drafted_counts_only_new_approvals(env) -> None:
    bundle, store, _, source, _ = env
    source.feed(_event())
    bundle.runtime.start()
    assert (await bundle.runtime.step()).drafted == 1

    assert (await bundle.runtime.step()).drafted == 0
    assert len(store.list_pending_approvals()) == 1
