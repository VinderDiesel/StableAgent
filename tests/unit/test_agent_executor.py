"""D1/D2/D3/D5/D7 + 4.1 补充：run_id 只在第一个 step 边界注册；
映射只读结构化字段（D4 全量归 M2）。"""

from __future__ import annotations

import pytest

from stableagent.core.state import Config, Event, EventType
from stableagent.orchestration.errors import WorkerConfigError
from stableagent.orchestration.executor import AgentExecutor
from stableagent.orchestration.model import (
    Assignment,
    AssignmentAction,
    AssignmentStatus,
    ReportKind,
    Ticket,
    TicketAction,
)
from tests.support import FixedClock, RecordingSink, StubAgent


def _ticket() -> Ticket:
    return Ticket(
        id="tk-1", title="t", task="算一遍", created_at=1, mandate_id="md-1", status="queued"
    )


def _assignment() -> Assignment:
    return Assignment(id="as-1", ticket_id="tk-1", attempt=1, status="running")


def _checkpointed_config() -> Config:
    return Config(
        checkpoint={
            "backend": "sqlite",
            "auto_save_every": 1,
            "recovery_strategy": "resume",
            "sqlite_path": ":memory:",
        }
    )


def _executor(events: list[Event], sink: RecordingSink, *, config: Config | None = None):
    agent = StubAgent(events, config=config or _checkpointed_config())
    executor = AgentExecutor(
        sink=sink, agent_factory=lambda ticket, assignment: agent, clock=FixedClock()
    )
    return executor, agent


async def test_checkpoint_required_worker_is_refused_before_any_io() -> None:
    sink = RecordingSink()
    executor, agent = _executor([], sink, config=Config())  # 默认 backend=memory
    with pytest.raises(WorkerConfigError):
        await executor.launch(_assignment(), _ticket(), [])
    assert agent._events == [] and sink.reports == []  # 没有启动，也没有报告


async def test_run_id_is_registered_once_at_the_first_step_boundary() -> None:
    sink = RecordingSink()
    events = [
        Event(type=EventType.THINKING, data={"text": "…"}, step=1),
        Event(
            type=EventType.CHECKPOINT,
            data={"run_id": "r-9", "step": 1, "contract_version": "1.1.0", "saved_at": 0},
            step=1,
        ),
        Event(type=EventType.TOOL_RESULT, data={"tool": "echo"}, step=1),
        Event(type=EventType.DONE, data={"status": "succeeded"}, step=2),
    ]
    executor, _ = _executor(events, sink)
    await executor.launch(_assignment(), _ticket(), [])
    assert sink.run_ids == [("as-1", "r-9")]  # 只注册一次
    assert sink.reports == [("tk-1", "as-1", ReportKind.PROGRESS.value, 1)]
    assert sink.finished == [("as-1", AssignmentAction.END.value, None)]
    assert sink.ticket_actions == [("tk-1", TicketAction.DELIVER.value)]


async def test_paused_needs_human_maps_to_waiting_and_budget_to_blocked() -> None:
    sink = RecordingSink()
    paused = Event(type=EventType.PAUSED, data={"step": 2, "reason": "needs_human", "detail": None})
    executor, _ = _executor([paused], sink)
    await executor.launch(_assignment(), _ticket(), [])
    assert sink.ticket_actions == [("tk-1", TicketAction.WAIT.value)]

    sink2 = RecordingSink()
    budget = Event(type=EventType.PAUSED, data={"step": 3, "reason": "budget_exceeded"})
    executor2, _ = _executor([budget], sink2)
    await executor2.launch(_assignment(), _ticket(), [])
    assert sink2.ticket_actions == [("tk-1", TicketAction.BLOCK.value)]


async def test_error_event_maps_to_failed_assignment_with_reason() -> None:
    sink = RecordingSink()
    error = Event(type=EventType.ERROR, data={"reason": "provider_unavailable", "detail": "503"})
    executor, _ = _executor([error], sink)
    await executor.launch(_assignment(), _ticket(), [])
    assert sink.finished[0][1] == AssignmentAction.FAIL.value
    assert sink.finished[0][2] == "provider_unavailable"
    assert sink.ticket_actions == [("tk-1", TicketAction.FAIL.value)]


async def test_recover_uses_stream_recover_and_never_replays_events() -> None:
    sink = RecordingSink()
    events = [Event(type=EventType.DONE, data={"status": "succeeded"}, step=4)]
    executor, agent = _executor(events, sink)
    assignment = _assignment().model_copy(
        update={"worker_run_id": "r-7", "status": AssignmentStatus.INTERRUPTED}
    )
    await executor.recover(assignment, _ticket())
    assert agent.recovered_with == ["r-7"]
    assert sink.finished == [("as-1", AssignmentAction.END.value, None)]


async def test_thinking_and_tool_call_do_not_emit_reports_by_default() -> None:
    sink = RecordingSink()
    events = [
        Event(type=EventType.THINKING, data={"text": "x"}, step=1),
        Event(type=EventType.TOOL_CALL, data={"name": "echo"}, step=1),
        Event(type=EventType.DONE, data={"status": "succeeded"}, step=2),
    ]
    executor, _ = _executor(events, sink)
    await executor.launch(_assignment(), _ticket(), [])
    assert sink.reports == []  # D3：默认零噪音


async def test_protocol_methods_not_in_scope_raise_clearly() -> None:
    sink = RecordingSink()
    executor, _ = _executor([], sink)
    with pytest.raises(NotImplementedError):
        await executor.deliver(_assignment(), [_ticket()], [])
    with pytest.raises(NotImplementedError):
        await executor.cancel(_assignment())
