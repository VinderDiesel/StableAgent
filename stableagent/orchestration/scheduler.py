"""从 Store 推导可运行工作，经 outbox 领取 effect 驱动 Worker；内存里没有不可恢复的队列。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from stableagent.orchestration.errors import DuplicateLaunchError, UnknownAssignmentError
from stableagent.orchestration.executor import WorkerExecutor
from stableagent.orchestration.model import (
    Assignment,
    AssignmentStatus,
    Clock,
    Effect,
    EffectKind,
    Ticket,
)
from stableagent.orchestration.outbox import EffectDispatcher
from stableagent.orchestration.service import OrchestrationService
from stableagent.orchestration.store import Store


@dataclass(frozen=True)
class TickResult:
    enqueued: int
    dispatched: int
    running: int


class Scheduler:
    def __init__(
        self,
        *,
        store: Store,
        service: OrchestrationService,
        executor: WorkerExecutor,
        clock: Clock,
        max_concurrency: int = 1,
    ) -> None:
        self._store = store
        self._service = service
        self._executor = executor
        self._clock = clock
        self._max_concurrency = max_concurrency
        self._slots = asyncio.Semaphore(max_concurrency)
        # 键是 assignment.id：worker_run_id 在 Worker 启动前还不存在（ADR-04 决策 4.1）。
        # 本字典只作取消与去重句柄，可运行性的事实源永远是 Store。
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._dispatcher = EffectDispatcher(
            store, {EffectKind.LAUNCH_ASSIGNMENT: self._handle_launch}, clock=clock
        )

    def running_assignments(self) -> set[str]:
        return {key for key, task in self._tasks.items() if not task.done()}

    async def tick(self) -> TickResult:
        enqueued = self._service.enqueue_work_for_runnable_tickets(self._clock.now_ms())
        dispatched = await self._dispatcher.dispatch_due(limit=self._max_concurrency)
        return TickResult(
            enqueued=enqueued,
            dispatched=dispatched,
            running=len(self.running_assignments()),
        )

    async def drive(self, assignment: Assignment, ticket: Ticket) -> None:
        live = self.running_assignments()
        if assignment.id in live:
            raise DuplicateLaunchError(assignment.worker_run_id or assignment.id)
        self._tasks[assignment.id] = asyncio.create_task(self._guarded(assignment, ticket))

    async def _guarded(self, assignment: Assignment, ticket: Ticket) -> None:
        async with self._slots:  # 槽位由 Worker 任务自己持有，直到终态或异常
            if assignment.status is AssignmentStatus.INTERRUPTED:
                running = self._service.relaunch_assignment(assignment.id)
                await self._executor.recover(running, ticket)
            else:
                await self._executor.launch(assignment, ticket, [])

    async def _handle_launch(self, effect: Effect) -> None:
        payload = effect.payload
        assignment = self._store.get_assignment(payload["assignment_id"])
        ticket = self._store.get_ticket(payload["ticket_id"])
        if assignment is None or ticket is None:
            raise UnknownAssignmentError(payload["assignment_id"])
        await self.drive(assignment, ticket)

    async def drain(self, *, max_rounds: int = 50) -> list[TickResult]:
        results: list[TickResult] = []
        for _ in range(max_rounds):
            result = await self.tick()
            results.append(result)
            if result.enqueued == 0 and result.dispatched == 0 and not self.running_assignments():
                break
            await asyncio.sleep(0)
        return results
