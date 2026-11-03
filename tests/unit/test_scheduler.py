"""E1 + L7：可运行性只从 Store 推导；单 run 单驱动；M1 固定单槽（E2 的宽度归 M2.8）。"""

from __future__ import annotations

import asyncio

import pytest

from stableagent.orchestration.errors import (
    DeferrableEffectError,
    DuplicateLaunchError,
    UnknownAssignmentError,
)
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


async def test_committed_effect_survives_reopen_and_dispatches(env, tmp_path) -> None:
    store, service, executor, scheduler, clock = env
    ticket = _queued(service, "提交后崩")
    assignment = store.list_assignments(ticket.id)[0]
    effect = store.due_effects(clock.now_ms())[0]
    store.close()
    reopened = Store(tmp_path / "sched.db", clock=clock)  # 新"进程"：只重开库
    try:
        executor2 = StubExecutor(None)
        executor2.gate.set()
        service2 = OrchestrationService(reopened, clock=clock, ids=SequenceIds())
        scheduler2 = Scheduler(
            store=reopened, service=service2, executor=executor2, clock=clock, max_concurrency=1
        )
        result = await scheduler2.tick()
        await _wait_until_idle(scheduler2)
        assert result.dispatched == 1 and executor2.launched == [assignment.id]
        assert reopened.get_effect(effect.id).state is EffectState.SUCCEEDED
    finally:
        reopened.close()


async def test_orphan_running_effect_is_reclaimed_then_driven_once(env, tmp_path) -> None:
    store, service, executor, scheduler, clock = env
    ticket = _queued(service, "handler 前崩")
    assignment = store.list_assignments(ticket.id)[0]
    effect = store.due_effects(clock.now_ms())[0]
    with store.transaction():
        store.update_effect(
            effect.model_copy(update={"state": EffectState.RUNNING, "attempts": 1})
        )  # 模拟领取后崩溃
    store.close()
    reopened = Store(tmp_path / "sched.db", clock=clock)
    try:
        service2 = OrchestrationService(reopened, clock=clock, ids=SequenceIds())
        summary = service2.recover()  # reclaim 把它放回 pending
        assert summary.reclaimed_effects == 1
        executor2 = StubExecutor(service2)
        executor2.gate.set()
        scheduler2 = Scheduler(
            store=reopened, service=service2, executor=executor2, clock=clock, max_concurrency=1
        )
        await scheduler2.tick()
        await _wait_until_idle(scheduler2)
        assert executor2.launched.count(assignment.id) == 1  # 只重放一次（幂等键兜底）
    finally:
        reopened.close()


async def test_recover_leaves_ended_tickets_alone(env, tmp_path) -> None:
    store, service, executor, scheduler, clock = env
    ticket = _queued(service, "已完成")
    assignment = store.list_assignments(ticket.id)[0]
    service.finish_assignment(assignment.id, AssignmentAction.LAUNCH)
    service.finish_assignment(assignment.id, AssignmentAction.END)
    service.transition_ticket(ticket.id, TicketAction.START)  # queued->running
    service.transition_ticket(ticket.id, TicketAction.DELIVER)
    service.transition_ticket(ticket.id, TicketAction.COMPLETE)
    summary = service.recover()
    assert (summary.interrupted_assignments, summary.relaunched) == (0, 0)
    assert executor.launched == []  # E8：终态不复活


async def test_deadline_timeout_cancels_assignment_and_blocks_ticket(env) -> None:
    store, service, executor, scheduler, clock = env
    ticket = service.create_ticket("超预算的")
    service.queue_ticket(ticket.id, deadline_at=clock.now_ms())  # 相对时限 0：必然超时
    assignment = store.list_assignments(ticket.id)[0]
    await scheduler.tick()
    await _wait_until_idle(scheduler)  # wait_for 在首个 await 处开火
    assert store.get_assignment(assignment.id).status is AssignmentStatus.CANCELLED
    assert store.get_ticket(ticket.id).status is TicketStatus.BLOCKED
    blocked = [r for r in store.list_reports(ticket.id) if r.kind is ReportKind.BLOCKED]
    assert blocked and blocked[0].details["reason"] == "deadline_exceeded"
    assert executor.launched == [assignment.id]  # 起跑过、被取消，非空转


async def test_future_deadline_does_not_interfere(env) -> None:
    store, service, executor, scheduler, clock = env
    ticket = service.create_ticket("按时的")
    service.queue_ticket(ticket.id, deadline_at=clock.now_ms() + 600_000)
    assignment = store.list_assignments(ticket.id)[0]
    await scheduler.tick()
    executor.gate.set()
    await _wait_until_idle(scheduler)
    assert store.get_assignment(assignment.id).status is AssignmentStatus.RUNNING
    assert store.get_ticket(ticket.id).status is TicketStatus.RUNNING  # stub 未发终态事件


async def test_negative_remaining_budget_clamps_to_zero(env) -> None:
    store, service, executor, scheduler, clock = env
    ticket = service.create_ticket("迟到的")
    service.queue_ticket(ticket.id, deadline_at=clock.now_ms() - 5000)
    await scheduler.tick()
    await _wait_until_idle(scheduler)
    assert store.get_ticket(ticket.id).status is TicketStatus.BLOCKED


async def test_inflight_launch_effect_is_not_redispatched(env) -> None:
    store, service, executor, scheduler, clock = env
    ticket = _queued(service, "在途去重")
    assignment = store.list_assignments(ticket.id)[0]
    await scheduler.tick()
    await asyncio.sleep(0)  # Worker 已起跑并卡在 gate
    duplicate = store.enqueue_effect(
        Effect(
            id="ef-dupe",
            kind=EffectKind.LAUNCH_ASSIGNMENT,
            idempotency_key=f"launch:{assignment.id}:1:manual",
            payload={"assignment_id": assignment.id, "ticket_id": ticket.id},
            due_at=clock.now_ms(),
        )
    )
    result = await scheduler.tick()
    assert result.dispatched == 0  # L7：在途不重领
    assert store.get_effect(duplicate.id).attempts == 0
    executor.gate.set()
    await _wait_until_idle(scheduler)


async def test_deliver_effect_for_missing_ticket_is_voided(env) -> None:
    store, _, executor, scheduler, clock = env
    ghost = store.enqueue_effect(
        Effect(
            id="ef-ghost-input",
            kind=EffectKind.DELIVER_INPUT,
            idempotency_key="deliver:tk-ghost:1",
            payload={"ticket_id": "tk-ghost", "input_id": "in-ghost", "sequence": 1},
            due_at=clock.now_ms(),
        )
    )
    result = await scheduler.tick()
    assert result.dispatched == 1  # 门控放行、handler 空转领掉
    assert executor.delivered == []
    assert store.get_effect(ghost.id).state is EffectState.SUCCEEDED


async def test_deliver_waits_while_ticket_has_not_started(env) -> None:
    store, service, executor, scheduler, clock = env
    ticket = service.create_ticket("还没排队")
    service.submit_input(ticket.id, "太早了")  # OPEN 状态下先落 Input
    effect = next(e for e in store.due_effects(clock.now_ms()))
    result = await scheduler.tick()
    assert result.dispatched == 0  # open/queued：随 launch 一并进 task
    assert executor.delivered == []
    assert store.get_effect(effect.id).attempts == 0


async def test_cancel_interrupts_a_deadline_bounded_worker(env) -> None:
    store, service, executor, scheduler, clock = env
    ticket = service.create_ticket("有预算也要能取消")
    service.queue_ticket(ticket.id, deadline_at=clock.now_ms() + 600_000)
    assignment = store.list_assignments(ticket.id)[0]
    await scheduler.tick()
    await asyncio.sleep(0)  # Worker 卡在 gate，等待里带着 deadline 预算
    service.cancel_ticket(ticket.id)
    await scheduler.tick()
    assert executor.cancelled == [assignment.id]
    assert store.get_assignment(assignment.id).status is AssignmentStatus.CANCELLED
    assert store.get_ticket(ticket.id).status is TicketStatus.CANCELLED


async def test_launch_effect_for_cancelled_assignment_is_voided(env) -> None:
    store, service, executor, scheduler, clock = env
    ticket = _queued(service, "先取消再启动")
    assignment = store.list_assignments(ticket.id)[0]
    service.cancel_ticket(ticket.id)
    service.closeout_cancelled_assignment(assignment.id)
    launch = next(
        e for e in store.due_effects(clock.now_ms()) if e.kind is EffectKind.LAUNCH_ASSIGNMENT
    )
    await scheduler._handle_launch(launch)
    assert executor.launched == []  # 已收口：领掉 effect，不再冷启动


async def test_cancel_effect_for_missing_assignment_raises(env) -> None:
    _, _, _, scheduler, clock = env
    effect = Effect(
        id="ef-ghost-cancel",
        kind=EffectKind.CANCEL_ASSIGNMENT,
        idempotency_key="cancel:as-ghost",
        payload={"assignment_id": "as-ghost", "ticket_id": "tk-ghost"},
        due_at=clock.now_ms(),
    )
    with pytest.raises(UnknownAssignmentError):
        await scheduler._handle_cancel(effect)


async def test_deliver_without_pending_input_is_a_noop(env) -> None:
    store, service, executor, scheduler, clock = env
    ticket = _queued(service, "没有待投")
    effect = Effect(
        id="ef-empty-deliver",
        kind=EffectKind.DELIVER_INPUT,
        idempotency_key=f"deliver:{ticket.id}:99",
        payload={"ticket_id": ticket.id, "input_id": "in-none", "sequence": 99},
        due_at=clock.now_ms(),
    )
    await scheduler._handle_deliver(effect)  # 被相邻 effect 配对消费
    assert executor.delivered == []


async def test_deliver_without_live_assignment_defers(env) -> None:
    store, service, executor, scheduler, clock = env
    ticket = _queued(service, "驻留丢了")
    service.transition_ticket(ticket.id, TicketAction.START)  # queued->running（无 Worker）
    service.transition_ticket(ticket.id, TicketAction.WAIT)  # running->waiting
    item = service.submit_input(ticket.id, "找不到 Worker")
    effect = Effect(
        id="ef-orphan-deliver",
        kind=EffectKind.DELIVER_INPUT,
        idempotency_key=f"deliver:{ticket.id}:{item.sequence}",
        payload={"ticket_id": ticket.id, "input_id": item.id, "sequence": item.sequence},
        due_at=clock.now_ms(),
    )
    # WAITING 且没有 RUNNING Assignment 的驻留形状不成立：现在不能消费 ≠ 失败
    with pytest.raises(DeferrableEffectError):
        await scheduler._handle_deliver(effect)


async def test_exhausted_launch_and_cancel_effects_derive_structured_records(env) -> None:
    store, service, executor, scheduler, clock = env
    ticket = _queued(service, "打满重试")
    assignment = store.list_assignments(ticket.id)[0]
    service.finish_assignment(assignment.id, AssignmentAction.LAUNCH)
    service.transition_ticket(ticket.id, TicketAction.START)
    launch = Effect(
        id="ef-dead-launch",
        kind=EffectKind.LAUNCH_ASSIGNMENT,
        idempotency_key=f"launch:{assignment.id}:1",
        payload={"assignment_id": assignment.id, "ticket_id": ticket.id},
        due_at=clock.now_ms(),
        attempts=3,
    )
    scheduler._handle_exhausted(launch, "boom")
    assert store.get_assignment(assignment.id).status is AssignmentStatus.FAILED
    assert store.get_ticket(ticket.id).status is TicketStatus.FAILED

    other = _queued(service, "取消也打满")
    cancel = Effect(
        id="ef-dead-cancel",
        kind=EffectKind.CANCEL_ASSIGNMENT,
        idempotency_key="cancel:as-missing",
        payload={"assignment_id": "as-missing", "ticket_id": other.id},
        due_at=clock.now_ms(),
        attempts=3,
    )
    scheduler._handle_exhausted(cancel, "cancel boom")
    blocked = [r for r in store.list_reports(other.id) if r.kind is ReportKind.BLOCKED]
    assert blocked and blocked[0].details["effect_id"] == "ef-dead-cancel"
