"""WorkerExecutor 的外壳：把「先复检、后执行」钉在执行面（L5 的 launch/recover/retry）。

复检不过 → 先写结构化 BLOCKED 报告，再抛 `PermanentEffectError`：dispatcher 据此直接判死，
不把一个只会得到同样拒绝的动作留在退避队列里。`cancel` 不复检——它不启动任何东西。
方向锁：integrations → control → orchestration；真正的启动/恢复/投递仍由 inner 完成。
"""

from __future__ import annotations

from collections.abc import Sequence

from stableagent.control.audit import AuditWriter
from stableagent.control.model import Decision
from stableagent.control.policy import StaticPolicy
from stableagent.execution.effects import prepare_worktree_handler
from stableagent.execution.worktree import GitWorktreeManager
from stableagent.orchestration.errors import PermanentEffectError
from stableagent.orchestration.executor import WorkerExecutor
from stableagent.orchestration.model import (
    Assignment,
    Clock,
    Effect,
    IdFactory,
    Mandate,
    Report,
    ReportKind,
    SystemClock,
    Ticket,
    TicketInput,
    Uuid4Id,
)
from stableagent.orchestration.outbox import EffectHandler
from stableagent.orchestration.store import Store


class RepositoryWorkerExecutor:
    """在 inner 前面加一道 Mandate 复检；拒绝时 park（报告 + 永久失败），不启动 Worker。"""

    def __init__(
        self,
        inner: WorkerExecutor,
        *,
        store: Store,
        policy: StaticPolicy,
        audit: AuditWriter,
        clock: Clock | None = None,
        ids: IdFactory | None = None,
    ) -> None:
        self._inner = inner
        self._store = store
        self._policy = policy
        self._audit = audit
        self._clock = clock or SystemClock()
        self._ids = ids or Uuid4Id()

    async def launch(
        self, assignment: Assignment, ticket: Ticket, inputs: Sequence[TicketInput]
    ) -> None:
        self._guard(ticket, assignment, "retry" if assignment.attempt > 1 else "launch")
        await self._inner.launch(assignment, ticket, inputs)

    async def recover(self, assignment: Assignment, ticket: Ticket) -> None:
        self._guard(ticket, assignment, "recover")
        await self._inner.recover(assignment, ticket)

    async def deliver(
        self, assignment: Assignment, ticket: Ticket, inputs: Sequence[TicketInput]
    ) -> None:
        # deliver 走 core.aresume：仍是同一份 Mandate 的一次执行，按 launch 时机复检
        self._guard(ticket, assignment, "launch")
        await self._inner.deliver(assignment, ticket, inputs)

    async def cancel(self, assignment: Assignment) -> None:
        await self._inner.cancel(assignment)

    def _guard(self, ticket: Ticket, assignment: Assignment, when: str) -> Mandate:
        mandate = self._store.get_mandate(ticket.mandate_id)
        if mandate is None:
            raise PermanentEffectError(f"mandate_missing:{ticket.mandate_id}")
        decision = self._policy.revalidate(mandate, when=when)
        self._audit.revalidated(
            mandate, when, decision, ticket_id=ticket.id, assignment_id=assignment.id
        )
        if not decision.allowed:
            self._park(ticket, assignment, when, decision)
            raise PermanentEffectError(f"{decision.reason}:{when}")
        return mandate

    def _park(self, ticket: Ticket, assignment: Assignment, when: str, decision: Decision) -> None:
        """结构化记录拒绝现场：D4 只认字段，不回头解析 summary 文案。"""
        _blocked_report(
            self._store,
            self._ids,
            self._clock,
            ticket_id=ticket.id,
            assignment_id=assignment.id,
            when=when,
            decision=decision,
            label="worker",
        )


def guarded_prepare_worktree_handler(
    manager: GitWorktreeManager,
    store: Store,
    *,
    policy: StaticPolicy,
    audit: AuditWriter,
    clock: Clock | None = None,
    ids: IdFactory | None = None,
) -> EffectHandler:
    """在真正 prepare 之前补第六个 revalidate 时机；拒绝时 manager 一次都不被调用。"""
    inner = prepare_worktree_handler(manager, store)
    clock = clock or SystemClock()
    ids = ids or Uuid4Id()

    async def handle(effect: Effect) -> None:
        payload = effect.payload
        ticket_id = payload.get("ticket_id")
        if not isinstance(ticket_id, str):
            raise PermanentEffectError("payload_missing:ticket_id")
        ticket = store.get_ticket(ticket_id)
        if ticket is None:
            raise PermanentEffectError(f"ticket_missing:{ticket_id}")
        mandate = store.get_mandate(ticket.mandate_id)
        if mandate is None:
            raise PermanentEffectError(f"mandate_missing:{ticket.mandate_id}")
        assignment_id = payload.get("assignment_id")
        if not isinstance(assignment_id, str):
            raise PermanentEffectError("payload_missing:assignment_id")
        decision = policy.revalidate(mandate, when="worktree")
        audit.revalidated(
            mandate,
            "worktree",
            decision,
            ticket_id=ticket.id,
            assignment_id=assignment_id,
        )
        if not decision.allowed:
            _blocked_report(
                store,
                ids,
                clock,
                ticket_id=ticket.id,
                assignment_id=assignment_id,
                when="worktree",
                decision=decision,
                label="worktree",
            )
            raise PermanentEffectError(f"{decision.reason}:worktree")
        await inner(effect)

    return handle


def _blocked_report(
    store: Store,
    ids: IdFactory,
    clock: Clock,
    *,
    ticket_id: str,
    assignment_id: str,
    when: str,
    decision: Decision,
    label: str,
) -> None:
    """两个守卫共用的拒绝现场：字段口径只写一次（D4）。"""
    store.insert_report(
        Report(
            id=ids.new_id("rp"),
            ticket_id=ticket_id,
            assignment_id=assignment_id,
            kind=ReportKind.BLOCKED,
            summary=f"{label} parked: {decision.reason}",
            created_at=clock.now_ms(),
            details={"when": when, "policy_version": decision.policy_version},
        )
    )
