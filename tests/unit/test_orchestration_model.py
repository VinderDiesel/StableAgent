"""A1/A2/A4：字段语义、序列化 round-trip、模型级校验与确定性 ID。"""

from __future__ import annotations

import pytest

from stableagent.orchestration.errors import IllegalTransitionError
from stableagent.orchestration.model import (
    TERMINAL_TICKET_STATUSES,
    Assignment,
    AssignmentAction,
    AssignmentStatus,
    Dependency,
    EffectAction,
    EffectKind,
    EffectState,
    Mandate,
    Ticket,
    TicketAction,
    TicketInput,
    TicketStatus,
    assert_assignment_belongs_to,
    next_assignment_status,
    next_effect_status,
    next_ticket_status,
)
from tests.support import FixedClock, SequenceIds


def _ticket(ids: SequenceIds, clock: FixedClock) -> Ticket:
    return Ticket(
        id=ids.new_id("tk"),
        title="写一份恢复报告",
        task="统计 checkpoint 事件数量",
        created_at=clock.now_ms(),
        mandate_id=ids.new_id("md"),
    )


def test_ticket_round_trips_and_is_deterministic() -> None:
    ids, clock = SequenceIds(), FixedClock()
    ticket = _ticket(ids, clock)
    assert ticket.id == "tk-0001" and ticket.status.value == "open"
    assert Ticket.model_validate_json(ticket.model_dump_json()) == ticket


def test_mandate_defaults_are_the_p4_local_shape() -> None:
    mandate = Mandate(id="md-1", granted_at=0)
    assert (mandate.origin_kind, mandate.requester, mandate.policy_version) == (
        "local",
        "local",
        "p4-local-v1",
    )


def test_assignment_attempt_starts_at_one_and_rejects_zero() -> None:
    with pytest.raises(ValueError):
        Assignment(id="as-1", ticket_id="tk-1", attempt=0)


def test_worker_run_id_is_nullable_until_launch() -> None:
    assignment = Assignment(id="as-1", ticket_id="tk-1", attempt=1)
    assert assignment.worker_run_id is None
    assert assignment.model_copy(update={"worker_run_id": "r1"}).worker_run_id == "r1"


def test_dependency_rejects_self_reference() -> None:
    with pytest.raises(ValueError):
        Dependency(ticket_id="tk-1", depends_on_ticket_id="tk-1")


def test_assignment_belongs_to_a_ticket_by_construction() -> None:
    # ticket_id 无默认值：缺了就构造不出来，A4 的"必属某 Ticket"是结构保证而非运行时检查。
    with pytest.raises(ValueError):
        Assignment(id="as-1", attempt=1)


@pytest.mark.parametrize(
    ("current", "action", "expected"),
    [
        (TicketStatus.OPEN, TicketAction.QUEUE, TicketStatus.QUEUED),
        (TicketStatus.QUEUED, TicketAction.START, TicketStatus.RUNNING),
        (TicketStatus.RUNNING, TicketAction.WAIT, TicketStatus.WAITING),
        (TicketStatus.WAITING, TicketAction.START, TicketStatus.RUNNING),
        (TicketStatus.RUNNING, TicketAction.DELIVER, TicketStatus.READY),
        (TicketStatus.RUNNING, TicketAction.BLOCK, TicketStatus.BLOCKED),
        (TicketStatus.RUNNING, TicketAction.FAIL, TicketStatus.FAILED),
        (TicketStatus.READY, TicketAction.COMPLETE, TicketStatus.COMPLETED),
        (TicketStatus.OPEN, TicketAction.CANCEL, TicketStatus.CANCELLED),
    ],
)
def test_legal_ticket_transitions(current, action, expected) -> None:
    assert next_ticket_status(current, action) is expected


def test_ready_is_not_completed_and_completed_is_terminal() -> None:
    # A6：ready 只是"可收口"，必须显式 complete_ticket 才进 completed。
    assert TicketStatus.READY is not TicketStatus.COMPLETED
    assert next_ticket_status(TicketStatus.READY, TicketAction.COMPLETE) is TicketStatus.COMPLETED
    assert TicketStatus.READY not in TERMINAL_TICKET_STATUSES
    assert {TicketStatus.COMPLETED, TicketStatus.CANCELLED, TicketStatus.FAILED} == (
        TERMINAL_TICKET_STATUSES
    )
    with pytest.raises(IllegalTransitionError):
        next_ticket_status(TicketStatus.COMPLETED, TicketAction.CANCEL)


def test_terminal_assignment_may_have_no_run_id() -> None:
    # 注册发生在第一个 step 边界（ADR-04 决策 4.1），崩在注册之前的 Assignment 终态也没有 run_id。
    # 因此"启动后必填"不是模型不变量（RUNNING 早于注册就会误伤），而是 Task 7 的行为断言：
    # launch 路径必须把首个 CHECKPOINT 的 run_id 注册回来。
    ended = Assignment(
        id="as-1", ticket_id="tk-1", attempt=1, status=AssignmentStatus.ENDED, worker_run_id=None
    )
    assert ended.worker_run_id is None


def test_terminal_assignment_cannot_be_relaunched() -> None:
    assert next_assignment_status(AssignmentStatus.INTERRUPTED, AssignmentAction.RELAUNCH) is (
        AssignmentStatus.RUNNING
    )
    with pytest.raises(IllegalTransitionError):
        next_assignment_status(AssignmentStatus.ENDED, AssignmentAction.RELAUNCH)


def test_effect_claim_and_retry_paths() -> None:
    assert next_effect_status(EffectState.PENDING, EffectAction.CLAIM) is EffectState.RUNNING
    assert next_effect_status(EffectState.RUNNING, EffectAction.RETRY) is EffectState.RETRY_WAIT
    assert next_effect_status(EffectState.RETRY_WAIT, EffectAction.CLAIM) is EffectState.RUNNING
    with pytest.raises(IllegalTransitionError):
        next_effect_status(EffectState.SUCCEEDED, EffectAction.CLAIM)


def test_assignment_cross_record_check_rejects_foreign_ticket() -> None:
    ticket = Ticket(
        id="tk-1",
        title="t",
        task="u",
        created_at=0,
        mandate_id="md-1",
    )
    assert_assignment_belongs_to(ticket, Assignment(id="as-1", ticket_id="tk-1", attempt=1))
    with pytest.raises(IllegalTransitionError):
        assert_assignment_belongs_to(ticket, Assignment(id="as-2", ticket_id="tk-other", attempt=1))


def test_m2_effect_kinds_close_the_whitelist() -> None:
    values = {k.value for k in EffectKind}
    assert values == {
        "launch_assignment",
        "deliver_input",
        "cancel_assignment",
        "prepare_worktree",
    }  # C1 + ADR-05 决策 5：白名单只按里程碑点名扩容，P5/M2 加 prepare_worktree
    # L6：merge 与 auto-merge 在类型里不存在（值与成员名两个口径都钉死），不是"运行时开关拒绝"
    names = {k.name.lower() for k in EffectKind}
    assert {"merge", "enable_auto_merge", "auto_merge"}.isdisjoint(values | names)


def test_deadline_and_input_state_are_defaulted_compat_fields() -> None:
    assignment = Assignment(id="as-1", ticket_id="tk-1", attempt=1)
    assert assignment.deadline_at is None  # 加列即默认值：旧构造不受影响（兼容扩展规则）
    item = TicketInput(id="in-1", ticket_id="tk-1", sequence=1, payload="x", created_at=1)
    assert item.state == "pending"
    assert TicketInput.model_validate_json(item.model_dump_json()) == item


def test_blocked_can_be_started_by_delivery_wakeup() -> None:
    assert next_ticket_status(TicketStatus.BLOCKED, TicketAction.START) is TicketStatus.RUNNING


def test_failed_can_retry_but_cancelled_never() -> None:
    assert next_ticket_status(TicketStatus.FAILED, TicketAction.RETRY) is TicketStatus.QUEUED
    with pytest.raises(IllegalTransitionError):
        next_ticket_status(TicketStatus.CANCELLED, TicketAction.RETRY)
    with pytest.raises(IllegalTransitionError):
        next_ticket_status(TicketStatus.COMPLETED, TicketAction.RETRY)


def test_queued_can_be_blocked_by_dependency_policy() -> None:
    assert next_ticket_status(TicketStatus.QUEUED, TicketAction.BLOCK) is TicketStatus.BLOCKED
