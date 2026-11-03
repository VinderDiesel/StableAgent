"""P4 领域记录的唯一写入口。同步命令只做写入与入队，需要 I/O 的部分一律交给 outbox。

service 不触达 executor/scheduler（L6 后半）；executor 经 AssignmentSink 协议回写，
实现即本类的 register_run_id/add_report/finish_assignment/transition_ticket 四方法。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from stableagent.orchestration.errors import UnknownAssignmentError, UnknownTicketError
from stableagent.orchestration.model import (
    TERMINAL_TICKET_STATUSES,
    Assignment,
    AssignmentAction,
    AssignmentStatus,
    Clock,
    Effect,
    EffectKind,
    IdFactory,
    IllegalTransitionError,
    Mandate,
    Report,
    ReportKind,
    Ticket,
    TicketAction,
    TicketInput,
    TicketStatus,
    next_assignment_status,
    next_ticket_status,
)
from stableagent.orchestration.store import Store

_ASSIGNMENT_ENDED_STATUSES = (
    AssignmentStatus.ENDED,
    AssignmentStatus.FAILED,
    AssignmentStatus.CANCELLED,
    AssignmentStatus.INTERRUPTED,
)


@dataclass(frozen=True)
class TicketView:
    ticket: Ticket
    assignments: tuple[Assignment, ...]
    reports: tuple[Report, ...]
    inputs: tuple[TicketInput, ...]


@dataclass(frozen=True)
class RecoverySummary:
    interrupted_assignments: int
    reclaimed_effects: int
    relaunched: int


class OrchestrationService:
    def __init__(self, store: Store, *, clock: Clock, ids: IdFactory) -> None:
        self._store = store
        self._clock = clock
        self._ids = ids

    # ------------------------------------------------------------------ 命令
    def create_ticket(
        self, task: str, *, title: str | None = None, parent_id: str | None = None
    ) -> Ticket:
        now = self._clock.now_ms()
        mandate = Mandate(id=self._ids.new_id("md"), granted_at=now, scope=title or task[:40])
        ticket = Ticket(
            id=self._ids.new_id("tk"),
            title=title or task[:40],
            task=task,
            created_at=now,
            mandate_id=mandate.id,
            parent_id=parent_id,
        )
        with self._store.transaction():
            self._store.insert_mandate(mandate)
            self._store.insert_ticket(ticket)
        return ticket

    def queue_ticket(self, ticket_id: str) -> Ticket:
        """OPEN/BLOCKED -> QUEUED，并在同一事务里建 Assignment + launch effect（C2）。"""
        with self._store.transaction():
            ticket = self._require(ticket_id)
            status = next_ticket_status(ticket.status, TicketAction.QUEUE)
            now = self._clock.now_ms()
            updated = ticket.model_copy(update={"status": status, "queued_at": now})
            self._store.update_ticket(updated)
            attempt = len(self._store.list_assignments(ticket_id)) + 1
            assignment = Assignment(id=self._ids.new_id("as"), ticket_id=ticket_id, attempt=attempt)
            self._store.insert_assignment(assignment)
            self._store.enqueue_effect(
                Effect(
                    id=self._ids.new_id("ef"),
                    kind=EffectKind.LAUNCH_ASSIGNMENT,
                    idempotency_key=f"launch:{assignment.id}:{attempt}",
                    payload={"assignment_id": assignment.id, "ticket_id": ticket_id},
                    due_at=now,
                )
            )
            return updated

    def complete_ticket(self, ticket_id: str, summary: str | None = None) -> Ticket:
        return self._apply(ticket_id, TicketAction.COMPLETE, summary=summary)

    def transition_ticket(self, ticket_id: str, action: TicketAction) -> Ticket:
        return self._apply(ticket_id, action)

    def submit_input(self, ticket_id: str, text: str) -> TicketInput:
        """落库未消费 Input 并入队 deliver_input effect（C2 后半：输入写入后建投递）。"""
        with self._store.transaction():
            ticket = self._require(ticket_id)
            if ticket.status in TERMINAL_TICKET_STATUSES:
                raise IllegalTransitionError("ticket", ticket.status.value, "submit_input")
            now = self._clock.now_ms()
            sequence = len(self._store.list_inputs(ticket_id)) + 1
            item = TicketInput(
                id=self._ids.new_id("in"),
                ticket_id=ticket_id,
                sequence=sequence,
                kind="text",
                payload=text,
                created_at=now,
            )
            self._store.insert_input(item)
            if ticket.status is not TicketStatus.RUNNING:
                self._store.update_ticket(
                    ticket.model_copy(update={"woken_at": ticket.woken_at or now})
                )
            self._store.enqueue_effect(
                Effect(
                    id=self._ids.new_id("ef"),
                    kind=EffectKind.DELIVER_INPUT,
                    idempotency_key=f"deliver:{ticket_id}:{sequence}",
                    payload={"ticket_id": ticket_id, "input_id": item.id, "sequence": sequence},
                    due_at=now,
                )
            )
            return item

    # ------------------------------------------------------------------ sink（executor 的回写面）
    def register_run_id(self, assignment_id: str, worker_run_id: str) -> None:
        with self._store.transaction():
            assignment = self._require_assignment(assignment_id)
            self._store.update_assignment(
                assignment.model_copy(update={"worker_run_id": worker_run_id})
            )

    def add_report(
        self,
        *,
        ticket_id: str,
        assignment_id: str,
        kind: ReportKind,
        summary: str,
        event_step: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        with self._store.transaction():
            self._store.insert_report(
                Report(
                    id=self._ids.new_id("rp"),
                    ticket_id=ticket_id,
                    assignment_id=assignment_id,
                    kind=kind,
                    summary=summary,
                    created_at=self._clock.now_ms(),
                    event_step=event_step,
                    details=dict(details or {}),
                )
            )

    def finish_assignment(
        self, assignment_id: str, action: AssignmentAction, *, failure: str | None = None
    ) -> None:
        with self._store.transaction():
            assignment = self._require_assignment(assignment_id)
            status = next_assignment_status(assignment.status, action)
            ended_at = (
                self._clock.now_ms()
                if status in _ASSIGNMENT_ENDED_STATUSES
                else assignment.ended_at
            )
            self._store.update_assignment(
                assignment.model_copy(
                    update={"status": status, "failure": failure, "ended_at": ended_at}
                )
            )

    def wake_for_delivery(self, ticket_id: str) -> Ticket:
        """waiting|blocked -> running（START 边）：投递真正进入 Worker 时才翻态。

        已是 RUNNING（唤醒后连续投递）返回原票——woken_at 沿用 submit_input 已置的值。
        """
        with self._store.transaction():
            ticket = self._require(ticket_id)
            if ticket.status is TicketStatus.RUNNING:
                return ticket
            updated = ticket.model_copy(
                update={"status": next_ticket_status(ticket.status, TicketAction.START)}
            )
            self._store.update_ticket(updated)
            return updated

    def mark_input_consumed(self, ticket_id: str, input_id: str) -> None:
        """sink 面：投递进 Worker（内存里的 state 副本）之后回写 consumed_at/state。"""
        with self._store.transaction():
            for item in self._store.list_inputs(ticket_id):
                if item.id == input_id:
                    now = self._clock.now_ms()
                    self._store.update_input(
                        item.model_copy(
                            update={
                                "delivered_at": item.delivered_at or now,
                                "consumed_at": now,
                                "state": "consumed",
                            }
                        )
                    )
                    return

    # ------------------------------------------------------------------ 调度支撑
    def enqueue_work_for_runnable_tickets(self, now_ms: int) -> int:
        """QUEUED 且有 PENDING Assignment 才算可运行；启动即 Assignment/Ticket -> RUNNING。"""
        started = 0
        for ticket in self._store.list_runnable_tickets(now_ms):
            pending = [
                a
                for a in self._store.list_assignments(ticket.id)
                if a.status
                in (
                    AssignmentStatus.PENDING,
                    AssignmentStatus.INTERRUPTED,
                )
            ]
            if not pending:
                continue
            with self._store.transaction():
                assignment = pending[0]
                action = (
                    AssignmentAction.RELAUNCH
                    if assignment.status is AssignmentStatus.INTERRUPTED
                    else AssignmentAction.LAUNCH
                )
                self._store.update_assignment(
                    assignment.model_copy(
                        update={
                            "status": next_assignment_status(assignment.status, action),
                            "started_at": now_ms,
                        }
                    )
                )
                self._store.update_ticket(
                    ticket.model_copy(
                        update={"status": next_ticket_status(ticket.status, TicketAction.START)}
                    )
                )
                suffix = ":relaunch" if action is AssignmentAction.RELAUNCH else ""
                self._store.enqueue_effect(
                    Effect(
                        id=self._ids.new_id("ef"),
                        kind=EffectKind.LAUNCH_ASSIGNMENT,
                        idempotency_key=f"launch:{assignment.id}:{assignment.attempt}{suffix}",
                        payload={"assignment_id": assignment.id, "ticket_id": ticket.id},
                        due_at=now_ms,
                    )
                )
                started += 1
        return started

    def relaunch_assignment(self, assignment_id: str) -> Assignment:
        """INTERRUPTED -> RUNNING（RELAUNCH 边）；effect 已由 recover()/enqueue_work 入队。"""
        with self._store.transaction():
            assignment = self._require_assignment(assignment_id)
            status = next_assignment_status(assignment.status, AssignmentAction.RELAUNCH)
            updated = assignment.model_copy(
                update={"status": status, "started_at": self._clock.now_ms()}
            )
            self._store.update_assignment(updated)
            return updated

    def recover(self) -> RecoverySummary:
        """running→interrupted 后为每个 Ticket 重启一个 Assignment：入队带 :relaunch 后缀的
        effect（键不同、原 effect 不复活，C3）。INTERRUPTED 状态保留到被领取，
        由 Scheduler 经 relaunch_assignment() 翻回 RUNNING。"""
        now = self._clock.now_ms()
        interrupted = 0
        relaunched = 0
        for ticket in self._store.list_tickets(status=TicketStatus.RUNNING):
            for assignment in self._store.list_assignments(ticket.id):
                if assignment.status is not AssignmentStatus.RUNNING:
                    continue
                self.finish_assignment(assignment.id, AssignmentAction.INTERRUPT)
                interrupted += 1
                with self._store.transaction():
                    self._store.enqueue_effect(
                        Effect(
                            id=self._ids.new_id("ef"),
                            kind=EffectKind.LAUNCH_ASSIGNMENT,
                            idempotency_key=f"launch:{assignment.id}:{assignment.attempt}:relaunch",
                            payload={"assignment_id": assignment.id, "ticket_id": ticket.id},
                            due_at=now,
                        )
                    )
                    relaunched += 1
                break  # M1：每个 Ticket 只重启一个 Assignment
        reclaimed = self._store.reclaim_running_effects(now)
        return RecoverySummary(
            interrupted_assignments=interrupted, reclaimed_effects=reclaimed, relaunched=relaunched
        )

    # ------------------------------------------------------------------ 查询
    def get_ticket(self, ticket_id: str) -> TicketView:
        ticket = self._require(ticket_id)
        return TicketView(
            ticket=ticket,
            assignments=tuple(self._store.list_assignments(ticket_id)),
            reports=tuple(self._store.list_reports(ticket_id)),
            inputs=tuple(self._store.list_inputs(ticket_id)),
        )

    def list_tickets(
        self,
        *,
        status: TicketStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[TicketView]:
        return [
            self.get_ticket(ticket.id)
            for ticket in self._store.list_tickets(status=status, limit=limit, offset=offset)
        ]

    # ------------------------------------------------------------------ 私有
    def _require(self, ticket_id: str) -> Ticket:
        ticket = self._store.get_ticket(ticket_id)
        if ticket is None:
            raise UnknownTicketError(ticket_id)
        return ticket

    def _require_assignment(self, assignment_id: str) -> Assignment:
        assignment = self._store.get_assignment(assignment_id)
        if assignment is None:
            raise UnknownAssignmentError(assignment_id)
        return assignment

    def _apply(self, ticket_id: str, action: TicketAction, *, summary: str | None = None) -> Ticket:
        with self._store.transaction():
            ticket = self._require(ticket_id)
            status = next_ticket_status(ticket.status, action)
            ended = self._clock.now_ms() if status in TERMINAL_TICKET_STATUSES else ticket.ended_at
            updated = ticket.model_copy(update={"status": status, "ended_at": ended})
            self._store.update_ticket(updated)
            if summary is not None:
                assignments = self._store.list_assignments(ticket_id)
                if not assignments:
                    raise IllegalTransitionError(
                        "ticket", ticket.status.value, "summary_without_assignment"
                    )
                self._store.insert_report(
                    Report(
                        id=self._ids.new_id("rp"),
                        ticket_id=ticket_id,
                        assignment_id=assignments[-1].id,
                        kind=ReportKind.READY,
                        summary=summary,
                        created_at=self._clock.now_ms(),
                    )
                )
            return updated
