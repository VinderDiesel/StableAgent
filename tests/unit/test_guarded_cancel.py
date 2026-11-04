"""B3 + L3：守卫式取消在**单个** `store.transaction()` 里完成三件事，任一失败全回滚。

P4 的 `OrchestrationService.cancel_ticket` 一字不动（ADR-05 约束 1）：本文件只证明
`GuardedTicketOps` 的取消与它同形（同一状态机纯函数、同一 `cancel:{assignment}` 幂等键、
同一 payload），并额外撤销该票的 pending approval。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stableagent.control.approval import ApprovalService, GuardedTicketOps
from stableagent.control.model import Action, ApprovalStatus, Resource, resource_digest
from stableagent.orchestration.errors import IllegalTransitionError, UnknownTicketError
from stableagent.orchestration.model import (
    Assignment,
    AssignmentStatus,
    EffectKind,
    Ticket,
    TicketStatus,
)
from stableagent.orchestration.store import Store
from tests.support import FixedClock, SequenceIds

TICKET = "tk-0001"
OTHER = "tk-0002"


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock()


@pytest.fixture
def store(tmp_path: Path, clock: FixedClock):
    handle = Store(tmp_path / "guarded.db", clock=clock)
    yield handle
    handle.close()


def _ticket(store: Store, clock: FixedClock, *, ticket_id: str = TICKET) -> Ticket:
    ticket = Ticket(
        id=ticket_id,
        title="推分支",
        task="push sa-tk-1-a",
        created_at=clock.now_ms(),
        status=TicketStatus.QUEUED,
        mandate_id="md-0001",
    )
    store.insert_ticket(ticket)
    return ticket


def _assignment(
    store: Store,
    assignment_id: str,
    status: AssignmentStatus,
    *,
    ticket_id: str = TICKET,
    attempt: int = 1,
) -> None:
    store.insert_assignment(
        Assignment(id=assignment_id, ticket_id=ticket_id, attempt=attempt, status=status)
    )


def _approvals(store: Store, clock: FixedClock) -> ApprovalService:
    return ApprovalService(store, clock=clock, ids=SequenceIds())


def _ops(store: Store, clock: FixedClock) -> GuardedTicketOps:
    return GuardedTicketOps(store, _approvals(store, clock), clock=clock, ids=SequenceIds())


def _submit_pending(approvals: ApprovalService, action: Action = Action.PUSH_BRANCH) -> str:
    digest = resource_digest(action, Resource(kind="branch", extra={"name": "sa-tk-1-a"}))
    return approvals.submit(TICKET, action, digest, "high_risk").id


def test_guarded_cancel_rolls_back_all_three_on_error(
    store: Store, clock: FixedClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """在撤销 pending 处注入异常：票未取消、effect 未入队、approval 仍 pending（B3）。"""
    _ticket(store, clock)
    _assignment(store, "as-0001", AssignmentStatus.PENDING)
    approvals = _approvals(store, clock)
    approval_id = _submit_pending(approvals)

    def boom(self: ApprovalService, ticket_id: str) -> int:
        raise RuntimeError("注入：撤销环节失败")

    monkeypatch.setattr(ApprovalService, "revoke_pending", boom)
    with pytest.raises(RuntimeError, match="注入"):
        _ops(store, clock).cancel_ticket(TICKET, reason="人工中止")

    assert store.get_ticket(TICKET).status is TicketStatus.QUEUED
    assert store.due_effects(clock.now_ms()) == []
    assert store.get_approval(approval_id)["status"] == ApprovalStatus.PENDING.value
    assert store.in_transaction is False  # 异常路径也要把事务标志清掉


def test_guarded_cancel_revokes_pending_only(store: Store, clock: FixedClock) -> None:
    """未终态 Assignment 各入队一条 cancel effect；pending 撤销，approved/denied 历史保留。"""
    _ticket(store, clock)
    _assignment(store, "as-0001", AssignmentStatus.PENDING)
    _assignment(store, "as-0002", AssignmentStatus.INTERRUPTED, attempt=2)  # 已终态：不再入队
    approvals = _approvals(store, clock)
    pending_id = _submit_pending(approvals)
    approved_id = _submit_pending(approvals, Action.CREATE_PULL_REQUEST)
    denied_id = _submit_pending(approvals, Action.COMMENT_ISSUE)
    approvals.decide(approved_id, True, "octocat")
    approvals.decide(denied_id, False, "octocat")

    clock.advance(7)
    ops = _ops(store, clock)  # 一个 ID 工厂服务整个进程：两次取消共享序号
    ticket = ops.cancel_ticket(TICKET, reason="人工中止")
    assert (ticket.status, ticket.ended_at) == (TicketStatus.CANCELLED, clock.now_ms())
    assert store.get_ticket(TICKET).ended_at == clock.now_ms()

    effects = store.due_effects(clock.now_ms())
    assert [item.kind for item in effects] == [EffectKind.CANCEL_ASSIGNMENT]
    effect = effects[0]
    assert effect.idempotency_key == "cancel:as-0001"  # 与 P4 同形：键只由 assignment id 决定
    assert effect.payload == {
        "assignment_id": "as-0001",
        "ticket_id": TICKET,
        "reason": "人工中止",
    }
    assert effect.due_at == clock.now_ms()

    statuses = {item.id: item.status for item in approvals.for_ticket(TICKET)}
    assert statuses == {
        pending_id: ApprovalStatus.REVOKED,
        approved_id: ApprovalStatus.APPROVED,
        denied_id: ApprovalStatus.DENIED,
    }
    revoked = store.get_approval(pending_id)
    assert (revoked["status"], revoked["decided_by"], revoked["decided_at"]) == (
        "revoked",
        None,  # 撤销不是人工裁决：`decided_by` 留空，只记终态时刻
        clock.now_ms(),
    )

    # 另一张票不受影响：reason 可省略，payload 里就是 None
    _ticket(store, clock, ticket_id=OTHER)
    _assignment(store, "as-0003", AssignmentStatus.RUNNING, ticket_id=OTHER)
    quiet = ops.cancel_ticket(OTHER)  # reason 省略 → payload 里是 None
    assert quiet.status is TicketStatus.CANCELLED
    extra = [
        item
        for item in store.due_effects(clock.now_ms())
        if item.idempotency_key == "cancel:as-0003"
    ]
    assert [item.payload for item in extra] == [
        {"assignment_id": "as-0003", "ticket_id": OTHER, "reason": None}
    ]


def test_guarded_cancel_rejects_terminal_ticket(store: Store, clock: FixedClock) -> None:
    """终态票不可取消：状态机违规由 `next_ticket_status` 抛错，同事务的撤销一并回滚。"""
    ticket = _ticket(store, clock)
    _assignment(store, "as-0001", AssignmentStatus.ENDED)
    approvals = _approvals(store, clock)
    approval_id = _submit_pending(approvals)
    completed = ticket.model_copy(
        update={"status": TicketStatus.COMPLETED, "ended_at": clock.now_ms()}
    )
    store.update_ticket(completed)

    ops = _ops(store, clock)
    with pytest.raises(IllegalTransitionError):
        ops.cancel_ticket(TICKET)
    assert store.get_approval(approval_id)["status"] == ApprovalStatus.PENDING.value
    assert store.get_ticket(TICKET).status is TicketStatus.COMPLETED

    with pytest.raises(UnknownTicketError):
        ops.cancel_ticket("tk-none")
    assert store.in_transaction is False
