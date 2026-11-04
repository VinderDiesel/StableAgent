"""B1/B2/B4 的 Gate 半边：批准绑定 `(action, resource_digest)`、denied 不自愈、重启后仍可判定。"""

from __future__ import annotations

from pathlib import Path

import pytest

from stableagent.control.approval import HIGH_RISK_ACTIONS, ApprovalGate, ApprovalService
from stableagent.control.audit import AuditWriter
from stableagent.control.errors import ApprovalDecidedError, UnknownApprovalError
from stableagent.control.model import Action, ApprovalStatus, Resource, resource_digest
from stableagent.orchestration.store import Store
from tests.support import FixedClock, SequenceIds
from tests.unit.test_authorization_audit import _policy, _request

TICKET = "tk-0001"
BRANCH_A = Resource(kind="branch", extra={"name": "sa-tk-1-a"})
BRANCH_B = Resource(kind="branch", extra={"name": "sa-tk-1-b"})


def _digest(resource: Resource, action: Action = Action.PUSH_BRANCH) -> str:
    return resource_digest(action, resource)


def _mandate(policy, *, requires_approval: bool = True):
    return policy.create_mandate(
        policy.admit_explicit(_request()),
        actions=[
            Action.PUSH_BRANCH,
            Action.RUN_COMMAND,
            Action.READ_REPOSITORY,
            Action.CREATE_PULL_REQUEST,
        ],
        allowed_commands=["pytest"],
        requires_approval=requires_approval,
    )


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock()


@pytest.fixture
def store(tmp_path: Path, clock: FixedClock):
    handle = Store(tmp_path / "approval.db", clock=clock)
    yield handle
    handle.close()


def _env(store: Store, clock: FixedClock):
    """一套 (policy, approvals, gate)：审批读取走 ApprovalService，落审计走 AuditWriter。"""
    policy = _policy(clock)
    approvals = ApprovalService(store, clock=clock, ids=SequenceIds())
    audit = AuditWriter(policy, store, clock=clock, ids=SequenceIds())
    return policy, approvals, ApprovalGate(policy, approvals, audit)


def test_submit_is_idempotent_on_the_pending_key(store: Store, clock: FixedClock) -> None:
    """同一 `(ticket, action, digest)` 的 pending 行只有一条；终态行不占这个键。"""
    approvals = ApprovalService(store, clock=clock, ids=SequenceIds())
    first = approvals.submit(TICKET, Action.PUSH_BRANCH, _digest(BRANCH_A), "high_risk")
    again = approvals.submit(TICKET, Action.PUSH_BRANCH, _digest(BRANCH_A), "high_risk_later")
    assert (first.id, again.id) == ("ap-0001", "ap-0001")
    assert again.reason == "high_risk"  # 既有行不被后写的理由覆盖
    assert len(store.list_approvals(TICKET)) == 1

    changed = approvals.submit(TICKET, Action.PUSH_BRANCH, _digest(BRANCH_B), "high_risk")
    assert changed.id == "ap-0002"  # digest 变了就是另一件事

    decided = approvals.decide(changed.id, True, "octocat")
    assert (decided.status, decided.decided_by, decided.decided_at) == (
        ApprovalStatus.APPROVED,
        "octocat",
        clock.now_ms(),
    )
    reopened = approvals.submit(TICKET, changed.action, changed.resource_digest, "high_risk")
    assert reopened.id == "ap-0003"  # 已 approved 的行不阻止重新申请

    assert approvals.revoke_pending("tk-other") == 0
    assert approvals.revoke_pending(TICKET) == 2  # ap-0001 与 ap-0003 是仅有的 pending
    assert approvals.for_ticket(TICKET, status=ApprovalStatus.PENDING) == []
    assert [
        item.status for item in approvals.for_ticket(TICKET, status=ApprovalStatus.REVOKED)
    ] == [
        ApprovalStatus.REVOKED,
        ApprovalStatus.REVOKED,
    ]
    assert approvals.for_ticket(TICKET, status=ApprovalStatus.APPROVED)[0].id == "ap-0002"
    assert approvals.get("ap-missing") is None
    assert [item.action for item in approvals.for_ticket(TICKET)] == [Action.PUSH_BRANCH] * 3


def test_decide_twice_is_rejected(store: Store, clock: FixedClock) -> None:
    """任何终态（approved/denied/revoked）之后再 decide 都是 `ApprovalDecidedError`。"""
    approvals = ApprovalService(store, clock=clock, ids=SequenceIds())
    approved = approvals.submit(TICKET, Action.PUSH_BRANCH, _digest(BRANCH_A), "high_risk")
    approvals.decide(approved.id, True, "octocat")
    with pytest.raises(ApprovalDecidedError) as flip:
        approvals.decide(approved.id, False, "octocat")
    assert flip.value.code == "control.approval_decided"
    assert "approved" in flip.value.detail
    assert approvals.get(approved.id).status is ApprovalStatus.APPROVED  # 改判没有发生

    denied = approvals.submit(TICKET, Action.PUSH_BRANCH, _digest(BRANCH_B), "high_risk")
    approvals.decide(denied.id, False, "octocat")
    with pytest.raises(ApprovalDecidedError):
        approvals.decide(denied.id, True, "octocat")

    pending = approvals.submit(
        TICKET,
        Action.CREATE_PULL_REQUEST,
        _digest(BRANCH_A, Action.CREATE_PULL_REQUEST),
        "high_risk",
    )
    assert approvals.revoke_pending(TICKET) == 1
    with pytest.raises(ApprovalDecidedError) as after_revoke:
        approvals.decide(pending.id, True, "octocat")
    assert "revoked" in after_revoke.value.detail

    with pytest.raises(UnknownApprovalError) as missing:
        approvals.decide("ap-none", True, "octocat")
    assert missing.value.code == "control.unknown_approval"


def test_approved_binding_breaks_on_resource_change(store: Store, clock: FixedClock) -> None:
    """B1：批准绑定 `(action, resource_digest)`；同一动作换个资源，旧批准立即失效。"""
    policy, approvals, gate = _env(store, clock)
    mandate = _mandate(policy)

    cold = gate.check(mandate, Action.PUSH_BRANCH, BRANCH_A, ticket_id=TICKET)
    assert (cold.allowed, cold.reason) == (False, "approval_pending")

    approval = approvals.submit(TICKET, Action.PUSH_BRANCH, _digest(BRANCH_A), "high_risk")
    approvals.decide(approval.id, True, "octocat")
    bound = gate.check(mandate, Action.PUSH_BRANCH, BRANCH_A, ticket_id=TICKET)
    assert (bound.allowed, bound.reason) == (True, "approved")

    stale = gate.check(mandate, Action.PUSH_BRANCH, BRANCH_B, ticket_id=TICKET)
    assert (stale.allowed, stale.reason) == (False, "approval_stale")
    # 换回原资源仍然放行：失效的是那份批准对新资源，不是批准本身被吃掉
    assert gate.check(mandate, Action.PUSH_BRANCH, BRANCH_A, ticket_id=TICKET).allowed is True

    rows = store.list_audit(kind="authorize")
    assert [row["reason"] for row in rows] == [
        "approval_pending",
        "approved",
        "approval_stale",
        "approved",
    ]
    assert [row["decision"] for row in rows] == ["deny", "allow", "deny", "allow"]
    assert [row["action"] for row in rows] == ["push_branch"] * 4
    assert [row["ticket_id"] for row in rows] == [TICKET] * 4
    assert rows[0]["resource_digest"] == _digest(BRANCH_A)
    assert rows[2]["resource_digest"] == _digest(BRANCH_B)
    assert rows[2]["mandate_id"] == mandate.id


def test_denied_never_self_heals(store: Store, clock: FixedClock) -> None:
    """B2 的 Gate 半边：denied 之后重新申请、推进时钟、重开读取都不构成自动放行。"""
    policy, approvals, gate = _env(store, clock)
    mandate = _mandate(policy)
    approval = approvals.submit(TICKET, Action.PUSH_BRANCH, _digest(BRANCH_A), "high_risk")
    approvals.decide(approval.id, False, "octocat")

    for _ in range(3):
        resubmitted = approvals.submit(TICKET, Action.PUSH_BRANCH, _digest(BRANCH_A), "high_risk")
        assert resubmitted.status is ApprovalStatus.PENDING  # 允许再问一次
        verdict = gate.check(mandate, Action.PUSH_BRANCH, BRANCH_A, ticket_id=TICKET)
        assert (verdict.allowed, verdict.reason) == (False, "approval_denied")
        clock.advance(1)

    # 换动作绕过：RUN_COMMAND 也在授权集合里，但这份 Mandate 要求审批 → 仍是待批，不是放行
    other = gate.check(
        mandate, Action.RUN_COMMAND, Resource(kind="command", command="pytest"), ticket_id=TICKET
    )
    assert (other.allowed, other.reason) == (False, "approval_pending")
    # 换资源也不绕过：denied 锁的是 (ticket, action)，新 digest 只得到 approval_stale/pending 系
    moved = gate.check(mandate, Action.PUSH_BRANCH, BRANCH_B, ticket_id=TICKET)
    assert (moved.allowed, moved.reason) == (False, "approval_denied")
    assert [row["reason"] for row in store.list_audit(kind="authorize")] == [
        "approval_denied",
        "approval_denied",
        "approval_denied",
        "approval_pending",
        "approval_denied",
    ]


def test_high_risk_actions_gate_without_the_flag(store: Store, clock: FixedClock) -> None:
    """高危集是 Gate 的第二道：`requires_approval=False` 也拦；低危动作直通并留审计行。"""
    assert (
        frozenset(
            {
                Action.PUSH_BRANCH,
                Action.CREATE_PULL_REQUEST,
                Action.COMMENT_ISSUE,
                Action.COMMENT_PULL_REQUEST,
                Action.REQUEST_REVIEW,
            }
        )
        == HIGH_RISK_ACTIONS
    )
    policy, _, gate = _env(store, clock)
    mandate = policy.create_mandate(
        policy.admit_explicit(_request()),
        actions=sorted({item.value for item in Action} - {Action.ENABLE_AUTO_MERGE.value}),
        requires_approval=False,
    )
    for action in HIGH_RISK_ACTIONS:
        verdict = gate.check(mandate, action, Resource(kind="branch"), ticket_id=TICKET)
        assert (verdict.allowed, verdict.reason) == (False, "approval_pending"), action
    quiet = gate.check(
        mandate, Action.READ_REPOSITORY, Resource(kind="repository", extra={"ref": "main"})
    )
    # 低危 + 无审批要求：不看审批表；resource kind 没有 allowlist 维度，由 action 集合说话
    assert (quiet.allowed, quiet.reason) == (True, "granted")
    # 高危动作缺票面上下文时不猜归属：宁可拒，也不把批准错记到别的 Ticket 上
    orphan = gate.check(mandate, Action.PUSH_BRANCH, BRANCH_A)
    assert (orphan.allowed, orphan.reason) == (False, "approval_context_missing")
    assert [row["reason"] for row in store.list_audit()] == [
        "approval_pending",
        "approval_pending",
        "approval_pending",
        "approval_pending",
        "approval_pending",
        "granted",
        "approval_context_missing",
    ]


def test_approval_survives_reopen(tmp_path: Path, clock: FixedClock) -> None:
    """B4：批准是库里的行，不是内存状态——进程重启（重开 Store）后 Gate 仍判 approved。"""
    db_path = tmp_path / "reopen.db"
    store = Store(db_path, clock=clock)
    policy, approvals, gate = _env(store, clock)
    mandate = _mandate(policy)
    store.insert_mandate(mandate)
    approval = approvals.submit(TICKET, Action.PUSH_BRANCH, _digest(BRANCH_A), "high_risk")
    approvals.decide(approval.id, True, "octocat")
    store.close()

    reopened = Store(db_path, clock=clock)
    try:
        _, restored_approvals, restored_gate = _env(reopened, clock)
        stored = reopened.get_mandate(mandate.id)
        assert stored is not None and stored.requires_approval is True
        assert restored_approvals.get(approval.id).status is ApprovalStatus.APPROVED
        verdict = restored_gate.check(stored, Action.PUSH_BRANCH, BRANCH_A, ticket_id=TICKET)
        assert (verdict.allowed, verdict.reason) == (True, "approved")
        # 重启不改变已有行的状态，也不凭空多出审计行
        assert reopened.get_approval(approval.id)["decided_by"] == "octocat"
    finally:
        reopened.close()


def test_gate_passes_policy_denials_straight_through(store: Store, clock: FixedClock) -> None:
    """Gate 先跑 `policy.authorize`：未授予的动作到不了审批表，reason 保持策略侧码。"""
    policy, _, gate = _env(store, clock)
    mandate = _mandate(policy)
    denied = gate.check(mandate, Action.ENABLE_AUTO_MERGE, BRANCH_A, ticket_id=TICKET)
    assert (denied.allowed, denied.reason) == (False, "not_granted")
    expired = mandate.model_copy(update={"expires_at": clock.now_ms() - 1})
    late = gate.check(expired, Action.PUSH_BRANCH, BRANCH_A, ticket_id=TICKET)
    assert (late.allowed, late.reason) == (False, "mandate_expired")
    rows = store.list_audit(kind="authorize")
    assert [(row["action"], row["reason"], row["decision"]) for row in rows] == [
        ("enable_auto_merge", "not_granted", "deny"),
        ("push_branch", "mandate_expired", "deny"),
    ]
