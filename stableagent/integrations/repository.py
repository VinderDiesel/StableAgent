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
from stableagent.orchestration.errors import PermanentEffectError
from stableagent.orchestration.executor import WorkerExecutor
from stableagent.orchestration.model import (
    Assignment,
    Clock,
    IdFactory,
    Mandate,
    Report,
    ReportKind,
    SystemClock,
    Ticket,
    TicketInput,
    Uuid4Id,
)
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
        self._store.insert_report(
            Report(
                id=self._ids.new_id("rp"),
                ticket_id=ticket.id,
                assignment_id=assignment.id,
                kind=ReportKind.BLOCKED,
                summary=f"worker parked: {decision.reason}",
                created_at=self._clock.now_ms(),
                details={"when": when, "policy_version": decision.policy_version},
            )
        )
