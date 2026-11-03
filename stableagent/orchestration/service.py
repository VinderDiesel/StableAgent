"""P4 领域记录的唯一写入口。同步命令只做写入与入队，需要 I/O 的部分一律交给 outbox。

service 不触达 executor/scheduler（L6 后半）；executor 经 AssignmentSink 协议回写，
实现即本类的 register_run_id/add_report/finish_assignment/transition_ticket 四方法。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from stableagent.orchestration.errors import (
    CyclicDependencyError,
    UnknownAssignmentError,
    UnknownTicketError,
)
from stableagent.orchestration.model import (
    TERMINAL_TICKET_STATUSES,
    Assignment,
    AssignmentAction,
    AssignmentStatus,
    Clock,
    Dependency,
    Effect,
    EffectKind,
    EffectState,
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
    """字段清单即契约（ADR-04 决策 4）：增删字段都是破坏性变更，测试钉死三项。"""

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

    def queue_ticket(self, ticket_id: str, *, deadline_at: int | None = None) -> Ticket:
        """OPEN/BLOCKED -> QUEUED，并在同一事务里建 Assignment + launch effect（C2）。"""
        with self._store.transaction():
            ticket = self._require(ticket_id)
            status = next_ticket_status(ticket.status, TicketAction.QUEUE)
            now = self._clock.now_ms()
            updated = ticket.model_copy(update={"status": status, "queued_at": now})
            self._store.update_ticket(updated)
            attempt = len(self._store.list_assignments(ticket_id)) + 1
            assignment = Assignment(
                id=self._ids.new_id("as"),
                ticket_id=ticket_id,
                attempt=attempt,
                deadline_at=deadline_at,
            )
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

    def retry_ticket(self, ticket_id: str) -> Assignment:
        """failed -> queued + 新 Assignment（attempt+1）+ launch effect——一次重试一条新历史。"""
        with self._store.transaction():
            ticket = self._require(ticket_id)
            status = next_ticket_status(ticket.status, TicketAction.RETRY)
            now = self._clock.now_ms()
            self._store.update_ticket(
                ticket.model_copy(
                    update={
                        "status": status,
                        "queued_at": now,
                        "woken_at": ticket.woken_at or now,
                    }
                )
            )
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
            return assignment

    def cancel_ticket(self, ticket_id: str, reason: str | None = None) -> Ticket:
        """只取消未结束 Ticket；每个未终态 Assignment 各入队 cancel_assignment，历史不删。"""
        with self._store.transaction():
            ticket = self._require(ticket_id)
            now = self._clock.now_ms()
            updated = ticket.model_copy(
                update={
                    "status": next_ticket_status(ticket.status, TicketAction.CANCEL),
                    "ended_at": now,
                }
            )
            self._store.update_ticket(updated)
            for assignment in self._store.list_assignments(ticket_id):
                if assignment.status in _ASSIGNMENT_ENDED_STATUSES:
                    continue
                self._store.enqueue_effect(
                    Effect(
                        id=self._ids.new_id("ef"),
                        kind=EffectKind.CANCEL_ASSIGNMENT,
                        idempotency_key=f"cancel:{assignment.id}",
                        payload={
                            "assignment_id": assignment.id,
                            "ticket_id": ticket_id,
                            "reason": reason,
                        },
                        due_at=now,
                    )
                )
            return updated

    def closeout_cancelled_assignment(self, assignment_id: str) -> None:
        """幂等收尾：Worker 已被取消的路径由 _guarded 记账，驻留路径在这里补记。"""
        with self._store.transaction():
            assignment = self._require_assignment(assignment_id)
            if assignment.status not in (
                AssignmentStatus.PENDING,
                AssignmentStatus.RUNNING,
                AssignmentStatus.INTERRUPTED,
            ):
                return
            self._store.update_assignment(
                assignment.model_copy(
                    update={
                        "status": next_assignment_status(
                            assignment.status, AssignmentAction.CANCEL
                        ),
                        "ended_at": self._clock.now_ms(),
                    }
                )
            )

    def add_dependency(
        self,
        ticket_id: str,
        depends_on: str,
        *,
        on_failure: Literal["block", "continue"] = "block",
    ) -> None:
        """E4：新边并入既有边表后自 depends_on BFS；能回到 ticket_id 即成环（直接/间接同码）。"""
        dependency = Dependency(
            ticket_id=ticket_id, depends_on_ticket_id=depends_on, on_failure=on_failure
        )
        with self._store.transaction():
            self._require(ticket_id)
            self._require(depends_on)
            edges: dict[str, list[str]] = {}
            for dep in [*self._store.list_all_dependencies(), dependency]:
                edges.setdefault(dep.ticket_id, []).append(dep.depends_on_ticket_id)
            cycle = self._path_to(edges, depends_on, ticket_id)
            if cycle is not None:
                raise CyclicDependencyError(ticket_id, [ticket_id, *cycle])
            self._store.insert_dependency(dependency)

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

    # ------------------------------------------------------------------ effect 失败派生
    def expire_assignment(self, assignment_id: str) -> None:
        """deadline 到期：Assignment 取消（已终态即 no-op）、Ticket running->blocked，报告必写。"""
        with self._store.transaction():
            assignment = self._require_assignment(assignment_id)
            now = self._clock.now_ms()
            if assignment.status is AssignmentStatus.RUNNING:
                self._store.update_assignment(
                    assignment.model_copy(
                        update={
                            "status": next_assignment_status(
                                assignment.status, AssignmentAction.CANCEL
                            ),
                            "ended_at": now,
                        }
                    )
                )
            ticket = self._store.get_ticket(assignment.ticket_id)
            if ticket is not None and ticket.status is TicketStatus.RUNNING:
                self._store.update_ticket(
                    ticket.model_copy(
                        update={"status": next_ticket_status(ticket.status, TicketAction.BLOCK)}
                    )
                )
            self._store.insert_report(
                Report(
                    id=self._ids.new_id("rp"),
                    ticket_id=assignment.ticket_id,
                    assignment_id=assignment_id,
                    kind=ReportKind.BLOCKED,
                    summary="deadline 超时",
                    created_at=now,
                    details={
                        "reason": "deadline_exceeded",
                        "assignment_id": assignment_id,
                        "deadline_at": assignment.deadline_at,
                    },
                )
            )

    def fail_unstarted_assignment(
        self, assignment_id: str, detail: str, *, reason: str = "effect_exhausted"
    ) -> None:
        """launch effect 打满重试或 Worker 起不来：Assignment/Ticket 一并落 failed。"""
        with self._store.transaction():
            assignment = self._store.get_assignment(assignment_id)
            if assignment is None:
                return
            now = self._clock.now_ms()
            if assignment.status is AssignmentStatus.RUNNING:
                self._store.update_assignment(
                    assignment.model_copy(
                        update={
                            "status": next_assignment_status(
                                assignment.status, AssignmentAction.FAIL
                            ),
                            "failure": detail,
                            "ended_at": now,
                        }
                    )
                )
            ticket = self._store.get_ticket(assignment.ticket_id)
            if ticket is not None and ticket.status in (
                TicketStatus.RUNNING,
                TicketStatus.WAITING,
            ):
                self._store.update_ticket(
                    ticket.model_copy(
                        update={
                            "status": next_ticket_status(ticket.status, TicketAction.FAIL),
                            "ended_at": now,
                        }
                    )
                )
            self._store.insert_report(
                Report(
                    id=self._ids.new_id("rp"),
                    ticket_id=assignment.ticket_id,
                    assignment_id=assignment_id,
                    kind=ReportKind.FAILED,
                    summary="effect 耗尽",
                    created_at=now,
                    details={"reason": reason, "detail": detail},
                )
            )

    def block_ticket_for_delivery_failure(
        self, ticket_id: str, detail: str, *, effect_id: str
    ) -> None:
        """投递 effect 打满：运行中/暂停中的 Ticket 落 blocked，READY/OPEN/QUEUED 只留报告。"""
        with self._store.transaction():
            ticket = self._require(ticket_id)
            now = self._clock.now_ms()
            if ticket.status in (TicketStatus.RUNNING, TicketStatus.WAITING):
                self._store.update_ticket(
                    ticket.model_copy(
                        update={"status": next_ticket_status(ticket.status, TicketAction.BLOCK)}
                    )
                )
            self._store.insert_report(
                Report(
                    id=self._ids.new_id("rp"),
                    ticket_id=ticket_id,
                    assignment_id=self._latest_assignment_id(ticket_id),
                    kind=ReportKind.BLOCKED,
                    summary="投递 effect 耗尽",
                    created_at=now,
                    details={
                        "reason": "effect_exhausted",
                        "effect_id": effect_id,
                        "detail": detail,
                    },
                )
            )

    def note_cancel_failure(self, ticket_id: str, detail: str, *, effect_id: str) -> None:
        """cancel effect 打满：只留结构化受阻报告，收尾仍由 closeout 幂等补齐。"""
        with self._store.transaction():
            self._require(ticket_id)
            self._store.insert_report(
                Report(
                    id=self._ids.new_id("rp"),
                    ticket_id=ticket_id,
                    assignment_id=self._latest_assignment_id(ticket_id),
                    kind=ReportKind.BLOCKED,
                    summary="取消 effect 耗尽",
                    created_at=self._clock.now_ms(),
                    details={
                        "reason": "effect_exhausted",
                        "effect_id": effect_id,
                        "detail": detail,
                    },
                )
            )

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
        """QUEUED + PENDING Assignment + 依赖全过才可运行；启动即 Assignment/Ticket -> RUNNING。"""
        started = 0
        tickets = self._store.list_runnable_tickets(now_ms)
        total = len(tickets)
        for index, ticket in enumerate(tickets):
            gate = self._dependency_gate(ticket)
            if gate == "unmet":
                continue
            if gate.startswith("broken:"):
                self._block_for_dependency(ticket, gate.split(":", 1)[1])
                continue
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
                # E7：同轮可领取的 launch 按选择序重打 due_at（全部 <= now），让被唤醒的
                # Ticket 先拿槽位——dispatcher 的 ORDER BY 只看 due_at/id，不认业务优先级。
                priority_due = now_ms - (total - index)
                launch = self._store.enqueue_effect(
                    Effect(
                        id=self._ids.new_id("ef"),
                        kind=EffectKind.LAUNCH_ASSIGNMENT,
                        idempotency_key=f"launch:{assignment.id}:{assignment.attempt}{suffix}",
                        payload={"assignment_id": assignment.id, "ticket_id": ticket.id},
                        due_at=priority_due,
                    )
                )
                if launch.state is EffectState.PENDING and launch.due_at != priority_due:
                    self._store.update_effect(launch.model_copy(update={"due_at": priority_due}))
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

    def _latest_assignment_id(self, ticket_id: str) -> str:
        assignments = self._store.list_assignments(ticket_id)
        if not assignments:
            raise UnknownAssignmentError(ticket_id)
        return assignments[-1].id

    @staticmethod
    def _path_to(edges: dict[str, list[str]], start: str, target: str) -> list[str] | None:
        """BFS + 父指针：edges[A]=[B] 表示 A 依赖 B。返回 start..target 的链，找不到即 None。"""
        parents: dict[str, str | None] = {start: None}
        queue = [start]
        while queue:
            node = queue.pop(0)
            for nxt in edges.get(node, ()):
                if nxt in parents:
                    continue
                parents[nxt] = node
                if nxt == target:
                    chain = [nxt]
                    while parents[chain[-1]] is not None:
                        chain.append(parents[chain[-1]])  # type: ignore[arg-type]
                    return list(reversed(chain))
                queue.append(nxt)
        return None

    def _dependency_gate(self, ticket: Ticket) -> str:
        """E5/E6：依赖全 completed 才 ready；上游失败按 on_failure 落 block 或 continue。"""
        for dep in self._store.list_dependencies(ticket.id):
            upstream = self._store.get_ticket(dep.depends_on_ticket_id)
            if upstream is None:
                return "unmet"
            if upstream.status is TicketStatus.COMPLETED:
                continue
            if upstream.status in TERMINAL_TICKET_STATUSES:
                if dep.on_failure == "block":
                    return f"broken:{upstream.id}"
                self._inject_dependency_failure_input(ticket.id, upstream)
                continue
            return "unmet"
        return "ready"

    def _inject_dependency_failure_input(self, ticket_id: str, upstream: Ticket) -> None:
        prefix = f"[依赖失败:{upstream.id}]"
        if any(item.payload.startswith(prefix) for item in self._store.list_inputs(ticket_id)):
            return  # 同一上游只注入一次：后续轮次直接放行
        with self._store.transaction():
            self._store.insert_input(
                TicketInput(
                    id=self._ids.new_id("in"),
                    ticket_id=ticket_id,
                    sequence=len(self._store.list_inputs(ticket_id)) + 1,
                    kind="text",
                    payload=f"{prefix} 上游 {upstream.status.value}",
                    created_at=self._clock.now_ms(),
                )
            )

    def _block_for_dependency(self, ticket: Ticket, dependency_id: str) -> None:
        with self._store.transaction():
            now = self._clock.now_ms()
            if ticket.status is TicketStatus.QUEUED:
                self._store.update_ticket(
                    ticket.model_copy(
                        update={
                            "status": next_ticket_status(ticket.status, TicketAction.BLOCK),
                            "ended_at": now,
                        }
                    )
                )
            self._store.insert_report(
                Report(
                    id=self._ids.new_id("rp"),
                    ticket_id=ticket.id,
                    assignment_id=self._latest_assignment_id(ticket.id),
                    kind=ReportKind.BLOCKED,
                    summary="依赖失败",
                    created_at=now,
                    details={"reason": "dependency_failed", "dependency": dependency_id},
                )
            )

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
