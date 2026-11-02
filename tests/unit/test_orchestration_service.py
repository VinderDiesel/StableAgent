"""A5 + L6：service 是唯一写入口；view 只由记录派生；queue_ticket 同事务落 Assignment + effect。"""

from __future__ import annotations

import pytest

from stableagent.orchestration.errors import IllegalTransitionError, UnknownTicketError
from stableagent.orchestration.model import (
    AssignmentAction,
    AssignmentStatus,
    EffectKind,
    ReportKind,
    TicketAction,
    TicketStatus,
)
from stableagent.orchestration.service import OrchestrationService
from stableagent.orchestration.store import Store
from tests.support import FixedClock, SequenceIds


@pytest.fixture
def service(tmp_path):
    clock = FixedClock()
    store = Store(tmp_path / "service.db", clock=clock)
    yield OrchestrationService(store, clock=clock, ids=SequenceIds()), store, clock
    store.close()


def test_create_then_queue_writes_assignment_and_effect_atomically(service) -> None:
    svc, store, clock = service
    ticket = svc.create_ticket("写一篇收口报告")
    assert ticket.status is TicketStatus.OPEN
    queued = svc.queue_ticket(ticket.id)
    assert queued.status is TicketStatus.QUEUED and queued.queued_at is not None
    assignments = store.list_assignments(ticket.id)
    assert [a.attempt for a in assignments] == [1]
    effects = store.due_effects(clock.now_ms())
    assert [e.kind for e in effects] == [EffectKind.LAUNCH_ASSIGNMENT]
    assert effects[0].idempotency_key == f"launch:{assignments[0].id}:1"


def test_view_is_derived_only_from_records(service) -> None:
    svc, store, _ = service
    ticket = svc.create_ticket("任务")
    view = svc.get_ticket(ticket.id)
    assert view.ticket.id == ticket.id and view.assignments == () and view.reports == ()
    assert svc.list_tickets()[0].ticket.id == ticket.id


def test_unknown_ticket_and_illegal_transition_are_stable_errors(service) -> None:
    svc, store, _ = service
    with pytest.raises(UnknownTicketError):
        svc.queue_ticket("tk-missing")
    ticket = svc.create_ticket("任务")
    svc.queue_ticket(ticket.id)
    with pytest.raises(IllegalTransitionError):
        svc.queue_ticket(ticket.id)  # queued -> queue 非法
    with pytest.raises(IllegalTransitionError):
        svc.complete_ticket(ticket.id)  # queued -> complete 非法（ready 才允许）
    assert store.get_ticket(ticket.id).status is TicketStatus.QUEUED  # 非法路径不落库


def test_sink_writes_reports_and_finishes_through_the_service(service) -> None:
    svc, store, clock = service
    ticket = svc.create_ticket("任务")
    svc.queue_ticket(ticket.id)
    svc.enqueue_work_for_runnable_tickets(clock.now_ms())
    assignment = store.list_assignments(ticket.id)[0]
    svc.register_run_id(assignment.id, "r-1")
    assert store.get_assignment(assignment.id).worker_run_id == "r-1"
    svc.add_report(
        ticket_id=ticket.id,
        assignment_id=assignment.id,
        kind=ReportKind.PROGRESS,
        summary="step 1",
        event_step=1,
        details={"tool": "echo"},
    )
    assert [r.kind for r in store.list_reports(ticket.id)] == [ReportKind.PROGRESS]
    svc.finish_assignment(assignment.id, AssignmentAction.END)
    svc.transition_ticket(ticket.id, TicketAction.DELIVER)
    assert store.get_ticket(ticket.id).status is TicketStatus.READY
    assert svc.complete_ticket(ticket.id, "完成").status is TicketStatus.COMPLETED


def test_recover_relaunches_interrupted_assignment(service) -> None:
    svc, store, clock = service
    ticket = svc.create_ticket("任务")
    svc.queue_ticket(ticket.id)
    assignment = store.list_assignments(ticket.id)[0]
    svc.register_run_id(assignment.id, "r-1")
    svc.finish_assignment(assignment.id, AssignmentAction.LAUNCH)  # -> running
    svc.transition_ticket(ticket.id, TicketAction.START)  # ticket -> running
    summary = svc.recover()
    assert summary.interrupted_assignments == 1
    assert store.get_assignment(assignment.id).status is AssignmentStatus.INTERRUPTED
    assert summary.relaunched == 1
    keys = [e.idempotency_key for e in store.due_effects(clock.now_ms())]
    assert f"launch:{assignment.id}:1:relaunch" in keys
