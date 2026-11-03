"""Worker 侧适配：把一次 Agent run 的事件映射成长期 Report。

只认 Core 的公开入口（stream / stream_recover / last_state / aresume(extra_input)）；run_id 在
第一个 step 边界注册，因为 Core 在 _drive() 内部才生成它（ADR-04 决策 4.1）。PAUSED 驻留
（park）不结束 Assignment：活实例留在内存里等人工输入，投递在暂停边界喂回（设计裁定 1）。
"""

from __future__ import annotations

from collections.abc import AsyncIterable, Callable, Sequence
from typing import Any, Protocol

from stableagent.core.agent import Agent
from stableagent.core.state import AgentState, AgentStatus, Event, EventType
from stableagent.orchestration.errors import DeferrableEffectError, WorkerConfigError
from stableagent.orchestration.model import (
    Assignment,
    AssignmentAction,
    Clock,
    ReportKind,
    Ticket,
    TicketAction,
    TicketInput,
)

AgentFactory = Callable[[Ticket, Assignment], Agent]

_REPORT_BY_EVENT: dict[EventType, ReportKind] = {EventType.TOOL_RESULT: ReportKind.PROGRESS}
_TERMINAL_ACTION = {
    EventType.DONE: (AssignmentAction.END, TicketAction.DELIVER),
    EventType.ERROR: (AssignmentAction.FAIL, TicketAction.FAIL),
}


class AssignmentSink(Protocol):
    """编排层唯一写入口的窄面：executor 不认识 OrchestrationService，只认识这个协议。"""

    def add_report(
        self,
        *,
        ticket_id: str,
        assignment_id: str,
        kind: ReportKind,
        summary: str,
        event_step: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None: ...

    def register_run_id(self, assignment_id: str, worker_run_id: str) -> None: ...

    def finish_assignment(
        self, assignment_id: str, action: AssignmentAction, *, failure: str | None = None
    ) -> None: ...

    def transition_ticket(self, ticket_id: str, action: TicketAction) -> None: ...

    def mark_input_consumed(self, ticket_id: str, input_id: str) -> None: ...


class WorkerExecutor(Protocol):
    async def launch(
        self, assignment: Assignment, ticket: Ticket, inputs: Sequence[TicketInput]
    ) -> None: ...

    async def deliver(
        self, assignment: Assignment, ticket: Ticket, inputs: Sequence[TicketInput]
    ) -> None: ...

    async def cancel(self, assignment: Assignment) -> None: ...

    async def recover(self, assignment: Assignment, ticket: Ticket) -> None: ...


class AgentExecutor:
    def __init__(
        self,
        *,
        sink: AssignmentSink,
        agent_factory: AgentFactory,
        clock: Clock,
        emit_progress_on: frozenset[EventType] = frozenset({EventType.TOOL_RESULT}),
    ) -> None:
        self._sink = sink
        self._agent_factory = agent_factory
        self._clock = clock
        self._emit_progress_on = frozenset(emit_progress_on)
        self._agents: dict[str, Agent] = {}  # park：Assignment.id -> 活实例（设计裁定 1）

    @staticmethod
    def _require_recoverable_worker(agent: Agent) -> None:
        checkpoint = agent.config.checkpoint
        if checkpoint.backend == "memory" or checkpoint.auto_save_every < 1:
            raise WorkerConfigError(
                "Worker 必须启用 step 边界 checkpoint"
                "（backend != memory 且 auto_save_every >= 1），"
                "否则 Assignment 没有可恢复的快照"
            )

    async def launch(
        self, assignment: Assignment, ticket: Ticket, inputs: Sequence[TicketInput]
    ) -> None:
        agent = self._agent_factory(ticket, assignment)
        self._require_recoverable_worker(agent)
        await self._drive(
            agent, ticket, assignment, agent.stream(_task_of(ticket, inputs)), consume=inputs
        )

    async def recover(self, assignment: Assignment, ticket: Ticket) -> None:
        if assignment.worker_run_id is None:
            raise WorkerConfigError("没有 worker_run_id 的 Assignment 不能走恢复，应重新 launch")
        agent = self._agent_factory(ticket, assignment)
        self._require_recoverable_worker(agent)
        await self._drive(agent, ticket, assignment, agent.stream_recover(assignment.worker_run_id))

    def parked_assignments(self) -> set[str]:
        """驻留中的 Assignment id（只读视图）：投递资格的事实源仍在 Store，这里只是运维可见性。"""
        return set(self._agents)

    async def deliver(
        self, assignment: Assignment, ticket: Ticket, inputs: Sequence[TicketInput]
    ) -> None:
        agent = self._agents.get(assignment.id)
        if agent is None:
            raise DeferrableEffectError(
                f"驻留实例不在：assignment={assignment.id}"
                "（进程崩溃后由 retry_ticket 走新 attempt 携带未消费输入）"
            )
        final = await agent.aresume(extra_input="\n\n".join(item.payload for item in inputs))
        for item in inputs:
            self._sink.mark_input_consumed(ticket.id, item.id)
        self._finish_from_state(assignment, ticket, final)

    async def cancel(self, assignment: Assignment) -> None:
        """只丢驻留实例；Assignment/Ticket 的状态写回在 scheduler/service 面。"""
        self._agents.pop(assignment.id, None)

    def _report_event(self, ticket: Ticket, assignment: Assignment, event: Event) -> None:
        kind = _REPORT_BY_EVENT.get(event.type)
        if kind is None or event.type not in self._emit_progress_on:
            return
        summary = f"{event.type.value} @ step {event.step}"
        self._sink.add_report(
            ticket_id=ticket.id,
            assignment_id=assignment.id,
            kind=kind,
            summary=summary,
            event_step=event.step,
            details=dict(event.data),
        )

    async def _drive(
        self,
        agent: Agent,
        ticket: Ticket,
        assignment: Assignment,
        events: AsyncIterable[Event],
        consume: Sequence[TicketInput] = (),
    ) -> None:
        run_id_registered = assignment.worker_run_id is not None
        first_event = True
        async for event in events:
            if first_event:
                # D6 首形态：随 launch 带入的未消费 Input 在 Worker 真正开跑的第一个事件处消费
                for item in consume:
                    self._sink.mark_input_consumed(ticket.id, item.id)
                first_event = False
            if not run_id_registered:
                run_id = _run_id_of(agent, event)
                if run_id is not None:
                    self._sink.register_run_id(assignment.id, run_id)
                    run_id_registered = True
            self._report_event(ticket, assignment, event)
            if event.type in _TERMINAL_ACTION:
                assignment_action, ticket_action = _TERMINAL_ACTION[event.type]
                failure = None
                if event.type is EventType.ERROR:
                    failure = str(
                        event.data.get("reason") or event.data.get("detail") or "worker_error"
                    )
                # Report 先于状态转换：sink 顺序即事件顺序（D4：只认结构化字段）
                self._sink.add_report(
                    ticket_id=ticket.id,
                    assignment_id=assignment.id,
                    kind=ReportKind.READY if event.type is EventType.DONE else ReportKind.FAILED,
                    summary="worker delivered"
                    if event.type is EventType.DONE
                    else f"worker failed: {failure}",
                    event_step=event.step,
                    details=dict(event.data),
                )
                self._agents.pop(assignment.id, None)
                self._sink.finish_assignment(assignment.id, assignment_action, failure=failure)
                self._sink.transition_ticket(ticket.id, ticket_action)
                return
            if event.type is EventType.PAUSED:
                reason = str(event.data.get("reason", "needs_human"))
                self._agents[assignment.id] = agent  # park：活实例才谈得上投递
                self._sink.add_report(
                    ticket_id=ticket.id,
                    assignment_id=assignment.id,
                    kind=ReportKind.WAITING_FOR_HUMAN
                    if reason == "needs_human"
                    else ReportKind.BLOCKED,
                    summary=f"worker parked: {reason}",
                    event_step=event.step,
                    details=dict(event.data),
                )
                self._sink.transition_ticket(
                    ticket.id,
                    TicketAction.WAIT if reason == "needs_human" else TicketAction.BLOCK,
                )
                return  # Assignment 保持 RUNNING：槽位随协程返回释放，驻留状态由 Ticket 表达

    def _finish_from_state(self, assignment: Assignment, ticket: Ticket, final: AgentState) -> None:
        """aresume 只返终态快照（无事件流）：收口按状态三分，全部经 transition_ticket 落态。

        偏差登记：续段再暂停的原因分类不可得（state.error 是自由文本，D4 禁止读文本判型），
        一律记 WAITING_FOR_HUMAN；首段流内 PAUSED 仍按 reason 分派 WAIT|BLOCK。
        """
        if final.status is AgentStatus.SUCCEEDED:
            self._agents.pop(assignment.id, None)
            self._sink.finish_assignment(assignment.id, AssignmentAction.END)
            self._sink.transition_ticket(ticket.id, TicketAction.DELIVER)
            return
        if final.status is AgentStatus.FAILED:
            self._agents.pop(assignment.id, None)
            self._sink.finish_assignment(
                assignment.id,
                AssignmentAction.FAIL,
                failure=str(final.error or "worker_error"),
            )
            self._sink.transition_ticket(ticket.id, TicketAction.FAIL)
            return
        if final.status is AgentStatus.PAUSED:
            self._sink.add_report(  # _agents 保留：重新驻留等下一次投递
                ticket_id=ticket.id,
                assignment_id=assignment.id,
                kind=ReportKind.WAITING_FOR_HUMAN,
                summary="worker re-parked after delivery",
                details={"step": final.step},
            )
            self._sink.transition_ticket(ticket.id, TicketAction.WAIT)


def _task_of(ticket: Ticket, inputs: Sequence[TicketInput]) -> str:
    if not inputs:
        return ticket.task
    return "\n\n".join([ticket.task, *(item.payload for item in inputs)])


def _run_id_of(agent: Agent, event: Event) -> str | None:
    """CHECKPOINT 事件带 run_id；否则退回 agent.last_state（两者都只在 step 边界出现）。"""
    from_row = event.data.get("run_id") if event.type is EventType.CHECKPOINT else None
    if isinstance(from_row, str) and from_row:
        return from_row
    state = getattr(agent, "last_state", None)
    return state.run_id if state is not None else None
