"""A5 + L6：service 是唯一写入口；view 只由记录派生；queue_ticket 同事务落 Assignment + effect。"""

from __future__ import annotations

import dataclasses

import pytest

from stableagent.orchestration.errors import (
    CyclicDependencyError,
    IllegalTransitionError,
    UnknownAssignmentError,
    UnknownTicketError,
)
from stableagent.orchestration.model import (
    AssignmentAction,
    AssignmentStatus,
    EffectKind,
    ReportKind,
    TicketAction,
    TicketStatus,
)
from stableagent.orchestration.service import OrchestrationService, RecoverySummary, TicketView
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


def test_submit_input_persists_pending_row_and_effect(service) -> None:
    svc, store, clock = service
    ticket = svc.create_ticket("任务")
    item = svc.submit_input(ticket.id, "追加线索")
    assert (item.sequence, item.kind, item.state) == (1, "text", "pending")
    effects = store.due_effects(clock.now_ms())
    assert [e.kind for e in effects] == [EffectKind.DELIVER_INPUT]
    assert effects[0].idempotency_key == f"deliver:{ticket.id}:1"
    svc.mark_input_consumed(ticket.id, item.id)
    fresh = svc.get_ticket(ticket.id).inputs[0]
    assert (fresh.state, fresh.consumed_at is not None) == ("consumed", True)


def test_submit_input_wakes_queued_and_refuses_terminal(service) -> None:
    svc, store, _ = service
    queued = svc.create_ticket("排队")
    svc.queue_ticket(queued.id)
    clock = svc._clock
    other = svc.create_ticket("更早")
    svc.queue_ticket(other.id)
    svc.submit_input(queued.id, "唤醒")
    assert store.get_ticket(queued.id).woken_at is not None  # 优先级键生效（Task 18 锁死）
    assert [t.id for t in store.list_runnable_tickets(clock.now_ms())] == [queued.id, other.id]
    done = svc.create_ticket("已完成")
    svc.queue_ticket(done.id)
    svc.transition_ticket(done.id, TicketAction.START)
    svc.transition_ticket(done.id, TicketAction.DELIVER)
    svc.complete_ticket(done.id)
    with pytest.raises(IllegalTransitionError):  # 已结束写入（G4 一条腿）
        svc.submit_input(done.id, "太晚了")
    with pytest.raises(UnknownTicketError):
        svc.submit_input("tk-missing", "未知")


def test_second_input_sequence_increments_per_ticket(service) -> None:
    svc, _, _ = service
    ticket = svc.create_ticket("任务")
    first = svc.submit_input(ticket.id, "线索一")
    second = svc.submit_input(ticket.id, "线索二")
    assert (first.sequence, second.sequence) == (1, 2)


def test_retry_ticket_adds_an_attempt_and_keeps_history(service) -> None:
    svc, store, clock = service
    ticket = svc.create_ticket("要重来的")
    svc.queue_ticket(ticket.id)
    first = store.list_assignments(ticket.id)[0]
    svc.finish_assignment(first.id, AssignmentAction.LAUNCH)
    svc.transition_ticket(ticket.id, TicketAction.FAIL)
    svc.finish_assignment(first.id, AssignmentAction.FAIL)
    retried = svc.retry_ticket(ticket.id)
    assert (retried.attempt, retried.id) == (2, "as-0002")
    assert store.get_ticket(ticket.id).status is TicketStatus.QUEUED
    assert store.get_ticket(ticket.id).woken_at is not None
    assert store.get_assignment(first.id).status is AssignmentStatus.FAILED  # 历史不覆盖
    keys = [e.idempotency_key for e in store.due_effects(clock.now_ms())]
    assert f"launch:{retried.id}:2" in keys  # 键含 attempt


def test_retry_only_from_failed(service) -> None:
    svc, _, _ = service
    open_ticket = svc.create_ticket("还没排队")
    with pytest.raises(IllegalTransitionError):
        svc.retry_ticket(open_ticket.id)
    done = svc.create_ticket("已收口")
    svc.queue_ticket(done.id)
    svc.transition_ticket(done.id, TicketAction.START)
    svc.transition_ticket(done.id, TicketAction.DELIVER)
    svc.complete_ticket(done.id)
    with pytest.raises(IllegalTransitionError):
        svc.retry_ticket(done.id)


def test_cancel_ticket_keeps_history_and_enqueues_effect(service) -> None:
    svc, store, clock = service
    ticket = svc.create_ticket("要取消的")
    svc.queue_ticket(ticket.id)
    assignment = store.list_assignments(ticket.id)[0]
    svc.finish_assignment(assignment.id, AssignmentAction.LAUNCH)
    svc.transition_ticket(ticket.id, TicketAction.START)
    cancelled = svc.cancel_ticket(ticket.id, "用户撤回")
    assert cancelled.status is TicketStatus.CANCELLED and cancelled.ended_at is not None
    keys = [e.idempotency_key for e in store.due_effects(clock.now_ms())]
    assert f"cancel:{assignment.id}" in keys
    assert [a.id for a in store.list_assignments(ticket.id)] == [assignment.id]  # 不删历史
    with pytest.raises(IllegalTransitionError):
        svc.cancel_ticket(cancelled.id)  # 已结束写入
    with pytest.raises(IllegalTransitionError):
        svc.retry_ticket(ticket.id)  # cancelled 不复活


def test_closeout_is_idempotent(service) -> None:
    svc, store, _ = service
    ticket = svc.create_ticket("任务")
    svc.queue_ticket(ticket.id)
    assignment = store.list_assignments(ticket.id)[0]
    svc.finish_assignment(assignment.id, AssignmentAction.LAUNCH)
    svc.closeout_cancelled_assignment(assignment.id)
    svc.closeout_cancelled_assignment(assignment.id)
    assert store.get_assignment(assignment.id).status is AssignmentStatus.CANCELLED


def test_add_dependency_rejects_direct_and_indirect_cycles(service) -> None:
    svc, _, _ = service
    a = svc.create_ticket("A")
    b = svc.create_ticket("B")
    c = svc.create_ticket("C")
    svc.add_dependency(b.id, a.id)
    with pytest.raises(CyclicDependencyError):
        svc.add_dependency(a.id, b.id)  # 直接环
    svc.add_dependency(c.id, b.id)
    with pytest.raises(CyclicDependencyError):
        svc.add_dependency(a.id, c.id)  # 间接环 a<-c<-b<-a
    with pytest.raises(ValueError):
        svc.add_dependency(a.id, a.id)  # 自指：模型校验仍先于一切


def test_downstream_waits_until_all_dependencies_completed(service) -> None:
    svc, store, clock = service
    parent = svc.create_ticket("父")
    child = svc.create_ticket("子", parent_id=parent.id)  # E3：parent 关系只来自显式调用
    svc.add_dependency(child.id, parent.id)
    svc.queue_ticket(parent.id)
    svc.queue_ticket(child.id)
    assert svc.enqueue_work_for_runnable_tickets(clock.now_ms()) == 1  # 只有父可运行（E5）
    # enqueue_work 已把父翻成 RUNNING；直接把父推到完成：running->ready->completed
    svc.transition_ticket(parent.id, TicketAction.DELIVER)
    svc.complete_ticket(parent.id)
    assert svc.enqueue_work_for_runnable_tickets(clock.now_ms()) == 1  # 子解锁（E5）


def test_block_policy_derives_blocked_with_structured_reason(service) -> None:
    svc, store, clock = service
    upstream = svc.create_ticket("上游")
    downstream = svc.create_ticket("下游")
    svc.add_dependency(downstream.id, upstream.id, on_failure="block")
    svc.queue_ticket(upstream.id)
    svc.transition_ticket(upstream.id, TicketAction.START)
    svc.transition_ticket(upstream.id, TicketAction.FAIL)
    svc.queue_ticket(downstream.id)
    assert svc.enqueue_work_for_runnable_tickets(clock.now_ms()) == 0
    assert store.get_ticket(downstream.id).status is TicketStatus.BLOCKED
    blocked = [r for r in store.list_reports(downstream.id) if r.kind is ReportKind.BLOCKED]
    assert blocked[0].details == {"reason": "dependency_failed", "dependency": upstream.id}


def test_continue_policy_injects_failed_dependency_as_input(service) -> None:
    svc, store, clock = service
    upstream, downstream = svc.create_ticket("上游"), svc.create_ticket("下游")
    svc.add_dependency(downstream.id, upstream.id, on_failure="continue")
    svc.queue_ticket(upstream.id)
    svc.transition_ticket(upstream.id, TicketAction.START)
    svc.transition_ticket(upstream.id, TicketAction.FAIL)
    svc.queue_ticket(downstream.id)
    assert svc.enqueue_work_for_runnable_tickets(clock.now_ms()) == 1  # 照常开跑
    payloads = [i.payload for i in store.list_inputs(downstream.id)]
    assert any(p.startswith("[依赖失败:") for p in payloads)
    svc.enqueue_work_for_runnable_tickets(clock.now_ms())  # 再跑一轮
    injected = [
        item for item in store.list_inputs(downstream.id) if item.payload.startswith("[依赖失败:")
    ]
    assert len(injected) == 1  # 只注入一次


def test_child_tickets_come_only_from_explicit_calls(service) -> None:
    svc, _, _ = service
    parent = svc.create_ticket("父")
    child = svc.create_ticket("子", parent_id=parent.id)
    assert child.parent_id == parent.id


def test_recovery_summary_fields_are_final() -> None:
    names = [f.name for f in dataclasses.fields(RecoverySummary)]
    assert names == ["interrupted_assignments", "reclaimed_effects", "relaunched"]


def test_list_tickets_filters_pages_and_carries_views(service) -> None:
    svc, _, _ = service
    ids = [svc.create_ticket(f"任务{i}") for i in range(6)]
    for ticket in ids[:4]:
        svc.queue_ticket(ticket.id)
    queued = svc.list_tickets(status=TicketStatus.QUEUED, limit=2, offset=2)
    assert [v.ticket.id for v in queued] == [ids[2].id, ids[3].id]
    assert all(isinstance(view, TicketView) for view in queued)


def test_all_ten_commands_exist(service) -> None:
    svc, _, _ = service
    commands = {
        "create_ticket",
        "add_dependency",
        "queue_ticket",
        "submit_input",
        "retry_ticket",
        "cancel_ticket",
        "complete_ticket",
        "recover",
        "get_ticket",
        "list_tickets",
    }
    assert commands <= {n for n in dir(svc)}


def test_cross_ticket_report_is_refused(service) -> None:
    svc, store, _ = service
    a = svc.create_ticket("甲")
    b = svc.create_ticket("乙")
    svc.queue_ticket(a.id)
    assignment = store.list_assignments(a.id)[0]
    with pytest.raises(IllegalTransitionError):
        svc.add_report(
            ticket_id=b.id,
            assignment_id=assignment.id,
            kind=ReportKind.PROGRESS,
            summary="挂错了",
            event_step=1,
        )


def test_stable_error_matrix(service) -> None:
    svc, _, _ = service
    cases = [
        (lambda: svc.queue_ticket("tk-none"), UnknownTicketError),
        (lambda: svc.get_ticket("tk-none"), UnknownTicketError),
        (lambda: svc.submit_input("tk-none", "x"), UnknownTicketError),
        (lambda: svc.retry_ticket("tk-none"), UnknownTicketError),
        (lambda: svc.cancel_ticket("tk-none"), UnknownTicketError),
        (lambda: svc.add_dependency("tk-none", "tk-none2"), UnknownTicketError),
    ]
    for invoke, expected in cases:
        with pytest.raises(expected):
            invoke()
    ticket = svc.create_ticket("排队")
    svc.queue_ticket(ticket.id)
    with pytest.raises(IllegalTransitionError):
        svc.queue_ticket(ticket.id)  # queued 再 queue 非法
    with pytest.raises(IllegalTransitionError):
        svc.retry_ticket(ticket.id)  # queued 不可 retry


def test_cancel_skips_ended_history_and_cancels_live_assignments(service) -> None:
    svc, store, clock = service
    ticket = svc.create_ticket("重试后取消")
    svc.queue_ticket(ticket.id)
    first = store.list_assignments(ticket.id)[0]
    svc.transition_ticket(ticket.id, TicketAction.START)
    svc.finish_assignment(first.id, AssignmentAction.LAUNCH)
    svc.finish_assignment(first.id, AssignmentAction.FAIL)
    svc.transition_ticket(ticket.id, TicketAction.FAIL)
    svc.retry_ticket(ticket.id)
    svc.cancel_ticket(ticket.id)
    cancels = [
        e
        for e in store.due_effects(clock.now_ms(), limit=10)
        if e.kind is EffectKind.CANCEL_ASSIGNMENT
    ]
    assert [e.payload["assignment_id"] for e in cancels] == [
        store.list_assignments(ticket.id)[-1].id
    ]  # 已 ended 的 attempt 1 被跳过，只在途的 attempt 2 入队


def test_fail_unstarted_assignment_for_missing_row_is_a_noop(service) -> None:
    svc, store, _ = service
    svc.fail_unstarted_assignment("as-missing", "worker 起不来")
    assert store.list_tickets() == []  # 无记录可写，幂等空转


def test_finish_assignment_with_unknown_id_is_stable_error(service) -> None:
    svc, _, _ = service
    with pytest.raises(UnknownAssignmentError):
        svc.finish_assignment("as-missing", AssignmentAction.END)


def test_queued_ticket_without_live_assignment_is_skipped(service) -> None:
    svc, store, clock = service
    ticket = svc.create_ticket("陈旧排队")
    svc.queue_ticket(ticket.id)
    assignment = store.list_assignments(ticket.id)[0]
    svc.finish_assignment(assignment.id, AssignmentAction.LAUNCH)
    svc.finish_assignment(assignment.id, AssignmentAction.FAIL)
    assert svc.enqueue_work_for_runnable_tickets(clock.now_ms()) == 0  # 没有可起的 Assignment
    assert store.get_ticket(ticket.id).status is TicketStatus.QUEUED


def test_recover_skips_a_running_ticket_without_running_assignment(service) -> None:
    svc, store, _ = service
    ticket = svc.create_ticket("只翻票未发车")
    svc.queue_ticket(ticket.id)
    svc.transition_ticket(ticket.id, TicketAction.START)
    summary = svc.recover()
    assert summary.interrupted_assignments == 0
    assert store.list_assignments(ticket.id)[0].status is AssignmentStatus.PENDING


def test_complete_ready_ticket_without_assignments_is_refused(service) -> None:
    svc, store, _ = service
    ticket = svc.create_ticket("无 Assignment 的 ready")
    store.update_ticket(ticket.model_copy(update={"status": TicketStatus.READY}))
    with pytest.raises(IllegalTransitionError):
        svc.complete_ticket(ticket.id, summary="人工通过")
