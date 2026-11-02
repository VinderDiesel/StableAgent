"""Worker 侧适配：把一次 Agent run 的事件映射成长期 Report。

只认 Core 的公开入口（stream / stream_recover / last_state）；run_id 在第一个 step 边界注册，
因为 Core 在 _drive() 内部才生成它（ADR-04 决策 4.1）。PAUSED 也结束 Assignment：恢复不回放
事件，而是由 retry_ticket/submit_input（M2）创建新 Assignment 走 stream_recover。
"""

from __future__ import annotations

from collections.abc import AsyncIterable, Callable, Sequence
from typing import Any, Protocol

from stableagent.core.agent import Agent
from stableagent.core.state import Event, EventType
from stableagent.orchestration.errors import WorkerConfigError
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
        await self._drive(agent, ticket, assignment, agent.stream(_task_of(ticket, inputs)))

    async def recover(self, assignment: Assignment, ticket: Ticket) -> None:
        if assignment.worker_run_id is None:
            raise WorkerConfigError("没有 worker_run_id 的 Assignment 不能走恢复，应重新 launch")
        agent = self._agent_factory(ticket, assignment)
        self._require_recoverable_worker(agent)
        await self._drive(agent, ticket, assignment, agent.stream_recover(assignment.worker_run_id))

    async def deliver(
        self, assignment: Assignment, ticket: Ticket, inputs: Sequence[TicketInput]
    ) -> None:
        raise NotImplementedError("deliver_input 属于里程碑 M2（依赖 extra_input Core 扩展）")

    async def cancel(self, assignment: Assignment) -> None:
        raise NotImplementedError("cancel_assignment 属于里程碑 M2")

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
        self, agent: Agent, ticket: Ticket, assignment: Assignment, events: AsyncIterable[Event]
    ) -> None:
        run_id_registered = assignment.worker_run_id is not None
        async for event in events:
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
                self._sink.finish_assignment(assignment.id, assignment_action, failure=failure)
                self._sink.transition_ticket(ticket.id, ticket_action)
                return
            if event.type is EventType.PAUSED:
                reason = str(event.data.get("reason", "needs_human"))
                self._sink.finish_assignment(assignment.id, AssignmentAction.END)
                self._sink.transition_ticket(
                    ticket.id,
                    TicketAction.WAIT if reason == "needs_human" else TicketAction.BLOCK,
                )
                return


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
