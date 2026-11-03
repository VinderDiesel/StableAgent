"""D1/D2/D3/D5/D7 + 4.1 补充：run_id 只在第一个 step 边界注册；
映射只读结构化字段（D4 全量归 M2）。"""

from __future__ import annotations

import pytest

from stableagent.core.state import (
    AgentState,
    AgentStatus,
    Config,
    Event,
    EventType,
)
from stableagent.orchestration.errors import DeferrableEffectError, WorkerConfigError
from stableagent.orchestration.executor import AgentExecutor
from stableagent.orchestration.model import (
    Assignment,
    AssignmentAction,
    AssignmentStatus,
    ReportKind,
    Ticket,
    TicketAction,
    TicketInput,
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
    assert sink.reports == [
        ("tk-1", "as-1", ReportKind.PROGRESS.value, 1),
        ("tk-1", "as-1", ReportKind.READY.value, 2),  # Task 19：终态 ready 报告
    ]
    assert sink.finished == [("as-1", AssignmentAction.END.value, None)]
    assert sink.ticket_actions == [("tk-1", TicketAction.DELIVER.value)]


async def test_paused_budget_maps_to_blocked_and_parks() -> None:
    sink = RecordingSink()
    budget = Event(type=EventType.PAUSED, data={"step": 3, "reason": "budget_exceeded"})
    executor, _ = _executor([budget], sink)
    await executor.launch(_assignment(), _ticket(), [])
    assert sink.ticket_actions == [("tk-1", TicketAction.BLOCK.value)]
    assert sink.finished == []  # park：预算暂停同样不终结 Assignment
    assert [r[2] for r in sink.reports] == [ReportKind.BLOCKED.value]
    assert executor.parked_assignments() == {"as-1"}


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
    assert [r[2] for r in sink.reports] == [ReportKind.READY.value]  # 中间事件零噪音，只留终态


async def test_paused_assignment_parks_instead_of_ending() -> None:
    sink = RecordingSink()
    paused = Event(
        type=EventType.PAUSED, data={"step": 2, "reason": "needs_human", "detail": None}, step=2
    )
    executor, _ = _executor([paused], sink)
    await executor.launch(_assignment(), _ticket(), [])
    assert sink.finished == []  # 不再 finish_assignment(END)
    assert sink.ticket_actions == [("tk-1", TicketAction.WAIT.value)]
    assert [r[2] for r in sink.reports] == [ReportKind.WAITING_FOR_HUMAN.value]
    assert executor.parked_assignments() == {"as-1"}


async def test_deliver_resumes_parked_agent_and_consumes_input() -> None:
    sink = RecordingSink()
    paused = Event(
        type=EventType.PAUSED, data={"step": 2, "reason": "needs_human", "detail": None}, step=2
    )
    executor, agent = _executor([paused], sink)
    await executor.launch(_assignment(), _ticket(), [])
    agent.next_state = AgentState(
        run_id="run-1", agent_name="stub", status=AgentStatus.SUCCEEDED, messages=[]
    )
    item = TicketInput(
        id="in-1", ticket_id="tk-1", sequence=1, kind="text", payload="新线索", created_at=1
    )
    await executor.deliver(_assignment(), _ticket(), [item])
    assert agent.resumed_with == ["新线索"]
    assert sink.consumed == [("tk-1", "in-1")]
    assert sink.finished == [("as-1", AssignmentAction.END.value, None)]
    assert sink.ticket_actions == [
        ("tk-1", TicketAction.WAIT.value),
        ("tk-1", TicketAction.DELIVER.value),
    ]
    assert executor.parked_assignments() == set()  # 收口即除驻


async def test_reparked_segment_reports_waiting_for_human_without_classification() -> None:
    sink = RecordingSink()
    paused = Event(
        type=EventType.PAUSED, data={"step": 2, "reason": "needs_human", "detail": None}, step=2
    )
    executor, agent = _executor([paused], sink)
    await executor.launch(_assignment(), _ticket(), [])
    agent.next_state = AgentState(
        run_id="run-1", agent_name="stub", status=AgentStatus.PAUSED, messages=[]
    )
    item = TicketInput(
        id="in-1", ticket_id="tk-1", sequence=1, kind="text", payload="再补一条", created_at=1
    )
    await executor.deliver(_assignment(), _ticket(), [item])
    assert sink.finished == []  # 重新驻留：仍不终结
    assert [r[2] for r in sink.reports] == [
        ReportKind.WAITING_FOR_HUMAN.value,
        ReportKind.WAITING_FOR_HUMAN.value,
    ]  # 偏差登记：续段再暂停原因不可得，一律 waiting_for_human（D4 不读文本）
    assert sink.ticket_actions == [("tk-1", TicketAction.WAIT.value)] * 2
    assert executor.parked_assignments() == {"as-1"}


async def test_deliver_without_park_defers_instead_of_failing() -> None:
    executor, _ = _executor([], RecordingSink())
    with pytest.raises(DeferrableEffectError):
        await executor.deliver(_assignment(), _ticket(), [])


async def test_cancel_clears_park_without_touching_store() -> None:
    sink = RecordingSink()
    paused = Event(
        type=EventType.PAUSED, data={"step": 2, "reason": "needs_human", "detail": None}, step=2
    )
    executor, _ = _executor([paused], sink)
    await executor.launch(_assignment(), _ticket(), [])
    assert executor.parked_assignments() == {"as-1"}
    await executor.cancel(_assignment())
    assert executor.parked_assignments() == set()  # executor 只丢驻留，状态写回在 service
    assert sink.finished == []


async def test_done_emits_ready_report_with_verbatim_diagnostic_payload() -> None:
    sink = RecordingSink()
    done = Event(type=EventType.DONE, data={"status": "succeeded", "text": "全部失败了"}, step=3)
    executor, _ = _executor([done], sink)
    await executor.launch(_assignment(), _ticket(), [])
    assert sink.reports == [("tk-1", "as-1", ReportKind.READY.value, 3)]
    assert sink.ticket_actions == [("tk-1", TicketAction.DELIVER.value)]  # D4：文本不参与判定
    assert sink.finished == [("as-1", AssignmentAction.END.value, None)]


async def test_error_emits_failed_report_keyed_on_reason_field() -> None:
    sink = RecordingSink()
    error = Event(type=EventType.ERROR, data={"reason": "provider_unavailable"}, step=2)
    executor, _ = _executor([error], sink)
    await executor.launch(_assignment(), _ticket(), [])
    assert [r[2] for r in sink.reports] == [ReportKind.FAILED.value]
    assert sink.finished[0][1] == AssignmentAction.FAIL.value
    assert sink.finished[0][2] == "provider_unavailable"  # 只读 data.reason，不读正文文本
