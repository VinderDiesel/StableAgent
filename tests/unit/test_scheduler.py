"""E1 + L7：可运行性只从 Store 推导；单 run 单驱动；M1 固定单槽（E2 的宽度归 M2.8）。"""

from __future__ import annotations

import asyncio

import pytest

from stableagent.orchestration.errors import DuplicateLaunchError
from stableagent.orchestration.model import Ticket
from stableagent.orchestration.scheduler import Scheduler
from stableagent.orchestration.service import OrchestrationService
from stableagent.orchestration.store import Store
from tests.support import FixedClock, SequenceIds


class StubExecutor:
    def __init__(self) -> None:
        self.launched: list[str] = []
        self.gate = asyncio.Event()
        self.started = asyncio.Event()

    async def launch(self, assignment, ticket, inputs) -> None:
        self.launched.append(assignment.id)
        self.started.set()
        await self.gate.wait()

    async def deliver(self, assignment, ticket, inputs) -> None: ...

    async def cancel(self, assignment) -> None: ...

    async def recover(self, assignment, ticket) -> None: ...


@pytest.fixture
def env(tmp_path):
    clock, ids = FixedClock(), SequenceIds()
    store = Store(tmp_path / "sched.db", clock=clock)
    service = OrchestrationService(store, clock=clock, ids=ids)
    executor = StubExecutor()
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
