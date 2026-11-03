"""E1 + L7：可运行性只从 Store 推导；单 run 单驱动；M1 固定单槽（E2 的宽度归 M2.8）。"""

from __future__ import annotations

import asyncio

import pytest

from stableagent.orchestration.errors import DuplicateLaunchError, UnknownAssignmentError
from stableagent.orchestration.model import (
    AssignmentAction,
    AssignmentStatus,
    Effect,
    EffectKind,
    EffectState,
    ReportKind,
    Ticket,
    TicketAction,
    TicketStatus,
)
from stableagent.orchestration.scheduler import Scheduler
from stableagent.orchestration.service import OrchestrationService
from stableagent.orchestration.store import Store
from tests.support import FixedClock, SequenceIds


class StubExecutor:
    def __init__(self, service: OrchestrationService | None = None) -> None:
        self.launched: list[str] = []
        self.recovered: list[str] = []
        self.delivered: list[tuple[str, str]] = []
        self.cancelled: list[str] = []
        self.fail_launches = False
        self.fail_deliver = False
        self._service = service
        self.gate = asyncio.Event()
        self.started = asyncio.Event()

    async def launch(self, assignment, ticket, inputs) -> None:
        self.launched.append(assignment.id)
        self.started.set()
        if self.fail_launches:
            raise RuntimeError("worker refused to start")
        await self.gate.wait()

    async def deliver(self, assignment, ticket, inputs) -> None:
        if self.fail_deliver:
            raise RuntimeError("delivery exploded")
        for item in inputs:
            self.delivered.append((ticket.id, item.payload))
            assert self._service is not None
            self._service.mark_input_consumed(ticket.id, item.id)

    async def cancel(self, assignment) -> None:
        self.cancelled.append(assignment.id)

    async def recover(self, assignment, ticket) -> None:
        self.recovered.append(assignment.id)


@pytest.fixture
def env(tmp_path):
    clock, ids = FixedClock(), SequenceIds()
    store = Store(tmp_path / "sched.db", clock=clock)
    service = OrchestrationService(store, clock=clock, ids=ids)
    executor = StubExecutor(service)
    scheduler = Scheduler(
        store=store, service=service, executor=executor, clock=clock, max_concurrency=1
    )
    yield store, service, executor, scheduler, clock
    store.close()


def _queued(service, task: str) -> Ticket:
    return service.queue_ticket(service.create_ticket(task).id)


async def _wait_until_idle(scheduler: Scheduler, *, rounds: int = 20) -> None:
    """只用公开 API 等 Worker 结束：每轮让出一次事件循环，槽位释放当轮即可见。"""
    for _ in range(rounds):
        if not scheduler.running_assignments():
            return
        await asyncio.sleep(0)
    raise AssertionError("Worker 未在预期轮次内结束")


async def test_tick_starts_exactly_one_worker_per_concurrency_slot(env) -> None:
    store, service, executor, scheduler, _ = env
    for index in range(3):
        _queued(service, f"任务 {index}")
    result = await scheduler.tick()
    await asyncio.sleep(0)  # 让 create_task 的协程起跑，launch 记录对事件循环可见
    assert result.enqueued == 3 and result.dispatched == 1
    assert len(executor.launched) == 1 and scheduler.running_assignments()  # 槽位被占
    executor.gate.set()
    await _wait_until_idle(scheduler)  # 第一个 Worker 结束，槽位释放
    second = await scheduler.tick()
    await _wait_until_idle(scheduler)  # 新任务只是 create_task，让出循环后 launch 才记录
    assert second.dispatched == 1 and len(executor.launched) == 2


async def test_second_launch_of_the_same_assignment_is_refused(env) -> None:
    store, service, executor, scheduler, _ = env
    ticket = _queued(service, "只有一个")
    await scheduler.tick()
    assignment = store.list_assignments(ticket.id)[0]

    with pytest.raises(DuplicateLaunchError):
        await scheduler.drive(assignment, ticket)


async def test_nothing_runnable_means_no_effect_and_no_io(env) -> None:
    _, _, executor, scheduler, _ = env
    result = await scheduler.tick()
    assert result.enqueued == 0 and result.dispatched == 0 and executor.launched == []


async def test_queue_survives_reopen_without_memory_state(env, tmp_path) -> None:
    store, service, executor, scheduler, clock = env
    _queued(service, "重启也要跑")
    store.close()
    reopened = Store(tmp_path / "sched.db", clock=clock)
    try:
        assert [t.id for t in reopened.list_runnable_tickets(clock.now_ms())]  # 事实源在库里
    finally:
        reopened.close()


async def test_drain_launches_every_queued_worker_then_quiets(env) -> None:
    _, service, executor, scheduler, _ = env
    for index in range(3):
        _queued(service, f"任务 {index}")
    executor.gate.set()  # 桩 Worker 立刻结束，drain 靠轮次推进把队列跑空
    results = await scheduler.drain(max_rounds=20)
    assert not scheduler.running_assignments()
    assert results[-1].enqueued == 0 and results[-1].dispatched == 0
    assert len(executor.launched) == 3


async def test_interrupted_assignment_is_relaunched_via_recover(env) -> None:
    store, service, executor, scheduler, _ = env
    ticket = _queued(service, "被打断过")
    assignment = store.list_assignments(ticket.id)[0]
    interrupted = assignment.model_copy(update={"status": AssignmentStatus.INTERRUPTED})
    store.update_assignment(interrupted)
    executor.gate.set()
    # 崩溃现场语义：Assignment 仍是 INTERRUPTED 就被驱动（enqueue_work 路径会先行翻转，
    # 真实翻转在 _guarded 里由 relaunch_assignment 完成）。
    await scheduler.drive(interrupted, ticket)
    await _wait_until_idle(scheduler)
    assert executor.recovered == [assignment.id]  # 恢复走 recover，不再冷启动
    assert executor.launched == []
    assert store.get_assignment(assignment.id).status is AssignmentStatus.RUNNING


async def test_launch_effect_pointing_at_missing_assignment_raises(env) -> None:
    store, _, _, scheduler, clock = env
    effect = store.enqueue_effect(
        Effect(
            id="ef-ghost",
            kind=EffectKind.LAUNCH_ASSIGNMENT,
            idempotency_key="launch:as-ghost:1",
            payload={"assignment_id": "as-ghost", "ticket_id": "tk-ghost"},
            due_at=clock.now_ms(),
        )
    )
    with pytest.raises(UnknownAssignmentError):
        await scheduler._handle_launch(effect)


async def test_delivery_waits_for_park_boundary_and_keeps_sequence(env) -> None:
    store, service, executor, scheduler, clock = env
    executor.gate.clear()  # 保持阻塞，模拟"正在运行"
    ticket = _queued(service, "运行中的活")
    await scheduler.tick()  # launch effect 已领取，Worker 卡住
    await asyncio.sleep(0)
    service.submit_input(ticket.id, "线索一")
    clock.advance(1000)
    service.submit_input(ticket.id, "线索二")
    pending = [e for e in store.due_effects(clock.now_ms()) if e.kind is EffectKind.DELIVER_INPUT]
    assert len(pending) == 2
    await scheduler.tick()
    assert executor.delivered == []  # D6：运行中绝不投递
    for row in pending:  # 且门控不领、不耗 attempts
        assert store.get_effect(row.id).attempts == 0
    # 模拟到达暂停边界：Ticket -> waiting（Task 15 后由真实 PAUSED 驱动，这里用 service 面）
    service.transition_ticket(ticket.id, TicketAction.WAIT)
    first_effect, second_effect = pending  # due_at：线索一更早
    with store.transaction():
        store.update_effect(second_effect.model_copy(update={"due_at": 0}))  # 反转领取顺序
    executor.gate.set()  # Worker 收摊，槽位与门控都放行投递
    await _wait_until_idle(scheduler)
    await scheduler.tick()
    await scheduler.tick()
    assert [p for (t, p) in executor.delivered] == ["线索一", "线索二"]  # 领取乱序、消费保序
    assert store.get_ticket(ticket.id).status is TicketStatus.RUNNING  # 投递唤醒过


async def test_deliveries_of_different_tickets_run_in_one_round(env) -> None:
    store, service, executor, scheduler, clock = env
    for name in ("甲", "乙"):
        ticket = _queued(service, f"任务{name}")
        assignment = store.list_assignments(ticket.id)[0]
        service.finish_assignment(assignment.id, AssignmentAction.LAUNCH)  # 造出驻留形状
        service.transition_ticket(ticket.id, TicketAction.START)  # queued->running
        service.transition_ticket(ticket.id, TicketAction.WAIT)  # running->waiting（驻留形状）
        # 驻留形状下 launch effect 早已被真实 Worker 领掉：夹具把这条残账结清（fixture 面，
        # 断言不动），否则本轮会多领一次冷启动。
        leftover = [
            e
            for e in store.due_effects(clock.now_ms())
            if e.kind is EffectKind.LAUNCH_ASSIGNMENT and e.payload["ticket_id"] == ticket.id
        ]
        assert len(leftover) == 1
        with store.transaction():
            store.update_effect(leftover[0].model_copy(update={"state": EffectState.SUCCEEDED}))
        service.submit_input(ticket.id, f"线索{name}")
    result = await scheduler.tick()
    assert result.dispatched == 2
    assert {t for t, _ in executor.delivered} == {"tk-0001", "tk-0002"}  # 不同 Ticket 并行


async def test_cancel_releases_the_slot_for_the_next_ticket(env) -> None:
    store, service, executor, scheduler, clock = env
    first = _queued(service, "长跑")
    second = _queued(service, "等待")
    await scheduler.tick()  # 领走 first 的 launch（单槽被 gate 占住）
    await asyncio.sleep(0)
    service.cancel_ticket(first.id)
    result = await scheduler.tick()  # 领走 cancel：任务被取消、槽位归还
    assert result.dispatched >= 1
    executor.gate.set()  # 窗口放宽后 second 可能同轮已被领走：放行桩 Worker 才能等到槽位空
    await _wait_until_idle(scheduler)
    await scheduler.tick()
    await asyncio.sleep(0)
    assert executor.launched == [
        store.list_assignments(first.id)[0].id,
        store.list_assignments(second.id)[0].id,
    ]
    assert store.get_assignment(store.list_assignments(first.id)[0].id).status is (
        AssignmentStatus.CANCELLED
    )
    assert store.get_ticket(first.id).status is TicketStatus.CANCELLED


async def test_cancel_of_parked_worker_drops_the_park(env) -> None:
    # StubExecutor.cancel 记 self.cancelled：驻留路径无在途任务，handler 仍要调它清驻留
    store, service, executor, scheduler, clock = env
    ticket = _queued(service, "只排队")
    assignment = store.list_assignments(ticket.id)[0]
    service.finish_assignment(assignment.id, AssignmentAction.LAUNCH)  # pending->running
    service.cancel_ticket(ticket.id)
    await scheduler.tick()
    assert executor.cancelled == [assignment.id]
    assert executor.launched == []  # 已取消的 Assignment 不再被冷启动
    assert store.get_assignment(assignment.id).status is AssignmentStatus.CANCELLED


async def test_exhausted_launch_fails_assignment_and_writes_failed_report(env) -> None:
    store, service, executor, scheduler, clock = env
    executor.fail_launches = True
    ticket = _queued(service, "起不来的")
    assignment = store.list_assignments(ticket.id)[0]
    for _ in range(4):
        await scheduler.tick()
        await asyncio.sleep(0)  # 让 create_task 的 Worker 起跑（起不来就在这一轮记账）
        clock.advance(10_000)  # 越过 backoff 的 due_at
    assert store.get_assignment(assignment.id).status is AssignmentStatus.FAILED
    assert store.get_ticket(ticket.id).status is TicketStatus.FAILED
    assert ReportKind.FAILED in [r.kind for r in store.list_reports(ticket.id)]


async def test_exhausted_delivery_blocks_ticket_with_reason(env) -> None:
    store, service, executor, scheduler, clock = env
    ticket = _queued(service, "等投料的")
    assignment = store.list_assignments(ticket.id)[0]
    service.finish_assignment(assignment.id, AssignmentAction.LAUNCH)
    service.transition_ticket(ticket.id, TicketAction.START)  # queued->running
    service.transition_ticket(ticket.id, TicketAction.WAIT)  # running->waiting（驻留形状）
    leftover = [
        e
        for e in store.due_effects(clock.now_ms())
        if e.kind is EffectKind.LAUNCH_ASSIGNMENT and e.payload["ticket_id"] == ticket.id
    ]
    assert len(leftover) == 1
    with store.transaction():  # 驻留形状下 launch 已被真实 Worker 领掉：夹具结清残账
        store.update_effect(leftover[0].model_copy(update={"state": EffectState.SUCCEEDED}))
    executor.fail_deliver = True  # 真实失败才打满；Deferrable 顺延永不判死（Task 13 已锁）
    service.submit_input(ticket.id, "永远等不到的线索")
    for _ in range(4):
        await scheduler.tick()
        clock.advance(10_000)
    assert store.get_ticket(ticket.id).status is TicketStatus.BLOCKED
    blocked = [r for r in store.list_reports(ticket.id) if r.kind is ReportKind.BLOCKED]
    assert blocked and "effect_id" in blocked[0].details  # 结构化原因，非自然语言


class CountingExecutor(StubExecutor):
    """E2：记录同时在跑的 Worker 数与峰值；launch 仍卡 gate。"""

    def __init__(self, service: OrchestrationService | None = None) -> None:
        super().__init__(service)
        self.live = 0
        self.max_live = 0

    async def launch(self, assignment, ticket, inputs) -> None:
        self.live += 1
        self.max_live = max(self.max_live, self.live)
        try:
            await super().launch(assignment, ticket, inputs)
        finally:
            self.live -= 1


@pytest.fixture
def env2(tmp_path):
    clock, ids = FixedClock(), SequenceIds()
    store = Store(tmp_path / "two.db", clock=clock)
    service = OrchestrationService(store, clock=clock, ids=ids)
    executor = CountingExecutor(service)
    scheduler = Scheduler(
        store=store, service=service, executor=executor, clock=clock, max_concurrency=2
    )
    yield store, service, executor, scheduler, clock
    store.close()


async def test_two_slots_cap_concurrent_workers(env2) -> None:
    store, service, executor, scheduler, clock = env2
    for index in range(3):
        _queued(service, f"任务{index}")
    await scheduler.tick()
    for _ in range(4):  # 让 create_task 起跑（公开 API 轮询）
        await asyncio.sleep(0)
    assert executor.live == 2 and executor.max_live == 2  # 第三个在门外（E2）
    executor.gate.set()
    await _wait_until_idle(scheduler)
    await scheduler.tick()
    executor.gate.set()
    await _wait_until_idle(scheduler)
    assert len(executor.launched) == 3 and executor.max_live == 2


async def test_woken_ticket_precedes_older_queued_ones(env) -> None:
    store, service, executor, scheduler, clock = env
    _queued(service, "先排的")
    clock.advance(1000)
    newer = _queued(service, "后排的")
    service.submit_input(newer.id, "唤醒它")  # woken_at 置位（Task 12）
    await scheduler.tick()
    await asyncio.sleep(0)  # 让 create_task 的 Worker 起跑，launch 记录可见
    launched = [store.get_assignment(a).ticket_id for a in executor.launched]
    assert launched == [newer.id]  # E7：唤醒优先，不是 FIFO
