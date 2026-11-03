"""从 Store 推导可运行工作，经 outbox 领取 effect 驱动 Worker；内存里没有不可恢复的队列。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from stableagent.orchestration.errors import (
    DeferrableEffectError,
    DuplicateLaunchError,
    UnknownAssignmentError,
)
from stableagent.orchestration.executor import WorkerExecutor
from stableagent.orchestration.model import (
    TERMINAL_TICKET_STATUSES,
    Assignment,
    AssignmentAction,
    AssignmentStatus,
    Clock,
    Effect,
    EffectKind,
    Ticket,
    TicketStatus,
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
            store,
            {
                EffectKind.LAUNCH_ASSIGNMENT: self._handle_launch,
                EffectKind.DELIVER_INPUT: self._handle_deliver,
                EffectKind.CANCEL_ASSIGNMENT: self._handle_cancel,
            },
            clock=clock,
            eligible={
                EffectKind.LAUNCH_ASSIGNMENT: self._gate_launch,
                EffectKind.DELIVER_INPUT: self._gate_deliver,
            },
            on_exhausted=self._handle_exhausted,
        )

    def running_assignments(self) -> set[str]:
        return {key for key, task in self._tasks.items() if not task.done()}

    def _gate_launch(self, effect: Effect) -> bool:
        live = self.running_assignments()
        if effect.payload.get("assignment_id") in live:
            return False  # L7：在途去重
        return len(live) < self._max_concurrency  # E2：槽位按活任务数判，驻留不占位

    def _gate_deliver(self, effect: Effect) -> bool:
        """设计裁定 2：资格从 Store 推导——运行中的 Worker 绝不被抢占（D6）。"""
        ticket = self._store.get_ticket(effect.payload["ticket_id"])
        if ticket is None or ticket.status in TERMINAL_TICKET_STATUSES:
            return True  # 无主/终态作废：领掉空转，不留永久 due 垃圾
        if ticket.status in (TicketStatus.WAITING, TicketStatus.BLOCKED):
            return True  # 暂停边界到了
        if ticket.status is TicketStatus.RUNNING:
            live = self.running_assignments()
            # RUNNING 但无活 Worker：唤醒后连续投递的中间态（驻留协程已让位）
            return not any(a.id in live for a in self._store.list_assignments(ticket.id))
        return False  # open/queued：未消费 Input 随 launch 一并进 task，由启动点消费

    async def tick(self) -> TickResult:
        enqueued = self._service.enqueue_work_for_runnable_tickets(self._clock.now_ms())
        # 并发上界由 _gate_launch 的活任务计数守住；窗口放宽，跨 Ticket 的投递才能同轮领掉
        dispatched = await self._dispatcher.dispatch_due()
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
        async with self._slots:  # 槽位由 Worker 任务自己持有，直到终态或异常/超时
            try:
                await self._run_with_deadline(assignment, ticket)
            except asyncio.CancelledError:
                self._service.finish_assignment(assignment.id, AssignmentAction.CANCEL)
                raise
            except Exception as exc:  # Worker 起不来就派生失败：重试一次坏掉的启动没有意义
                self._service.fail_unstarted_assignment(
                    assignment.id, repr(exc), reason="worker_start_failed"
                )

    async def _run_with_deadline(self, assignment: Assignment, ticket: Ticket) -> None:
        if assignment.deadline_at is None:
            # 不额外包 task：launch 要落在本次调度内，drain 与测试的 sleep(0) 依赖这一点。
            await self._run_worker(assignment, ticket)
            return
        worker = asyncio.ensure_future(self._run_worker(assignment, ticket))
        try:
            # E9：3.10 兼容不用 asyncio.timeout；预算可为 0，所以用 wait() 而不是 wait_for()
            # ——后者在 timeout=0 时会在协程首步之前就掐掉，Worker 成空转。
            budget = max((assignment.deadline_at - self._clock.now_ms()) / 1000.0, 0.0)
            done, _ = await asyncio.wait({worker}, timeout=budget)
        except asyncio.CancelledError:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            raise
        if worker in done:
            worker.result()
            return
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        self._service.expire_assignment(assignment.id)

    async def _run_worker(self, assignment: Assignment, ticket: Ticket) -> None:
        if assignment.status is AssignmentStatus.INTERRUPTED:
            running = self._service.relaunch_assignment(assignment.id)
            await self._executor.recover(running, ticket)
            return
        # D6 首形态（空闲边界=启动点）：未消费 Input 进 task，AgentExecutor 在
        # 第一个事件处逐条回写消费。
        pending = [
            item
            for item in self._store.list_inputs(ticket.id, pending_only=True)
            if item.kind == "text"
        ]
        await self._executor.launch(assignment, ticket, pending)

    def _handle_exhausted(self, effect: Effect, detail: str) -> None:
        """真实失败打满重试（Deferrable 永不走到这里）：按 kind 派生结构化记录。"""
        payload = effect.payload
        if effect.kind is EffectKind.LAUNCH_ASSIGNMENT:
            self._service.fail_unstarted_assignment(payload["assignment_id"], detail)
        elif effect.kind is EffectKind.DELIVER_INPUT:
            self._service.block_ticket_for_delivery_failure(
                payload["ticket_id"], detail, effect_id=effect.id
            )
        else:
            self._service.note_cancel_failure(payload["ticket_id"], detail, effect_id=effect.id)

    async def _handle_launch(self, effect: Effect) -> None:
        payload = effect.payload
        assignment = self._store.get_assignment(payload["assignment_id"])
        ticket = self._store.get_ticket(payload["ticket_id"])
        if assignment is None or ticket is None:
            raise UnknownAssignmentError(payload["assignment_id"])
        if assignment.status in (
            AssignmentStatus.ENDED,
            AssignmentStatus.FAILED,
            AssignmentStatus.CANCELLED,
        ):
            return  # 已收口/已取消：领掉 effect，不再冷启动
        await self.drive(assignment, ticket)

    async def _handle_cancel(self, effect: Effect) -> None:
        assignment_id = effect.payload["assignment_id"]
        task = self._tasks.get(assignment_id)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)  # 槽位归还先于 handler 返回
        assignment = self._store.get_assignment(assignment_id)
        if assignment is None:
            raise UnknownAssignmentError(assignment_id)
        await self._executor.cancel(assignment)
        self._service.closeout_cancelled_assignment(assignment_id)

    async def _handle_deliver(self, effect: Effect) -> None:
        """设计裁定 3：一个 effect 只消费该 Ticket 最旧的未消费 Input（领取乱序、消费保序）。"""
        ticket = self._store.get_ticket(effect.payload["ticket_id"])
        if ticket is None or ticket.status in TERMINAL_TICKET_STATUSES:
            return  # 空转领掉：投递对象已不存在或已收口
        pending = [
            item
            for item in self._store.list_inputs(ticket.id, pending_only=True)
            if item.kind == "text"
        ]
        if not pending:
            return  # 被相邻 effect 按序号配对消费掉了
        assignment = next(
            (
                a
                for a in self._store.list_assignments(ticket.id)
                if a.status is AssignmentStatus.RUNNING
            ),
            None,
        )
        if assignment is None:
            raise DeferrableEffectError(f"ticket {ticket.id} 的 Worker 尚未驻留")
        woken = self._service.wake_for_delivery(ticket.id)
        await self._executor.deliver(assignment, woken, [pending[0]])

    async def drain(self, *, max_rounds: int = 50) -> list[TickResult]:
        results: list[TickResult] = []
        for _ in range(max_rounds):
            result = await self.tick()
            results.append(result)
            if result.enqueued == 0 and result.dispatched == 0 and not self.running_assignments():
                break
            await asyncio.sleep(0)
        return results
