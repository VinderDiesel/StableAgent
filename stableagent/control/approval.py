"""审批服务、审批门与守卫式取消（规格 2.2、ADR-05 决策 3；步骤 1.7 的 B1~B4）。

依赖方向仍是 control → orchestration：审批行与审计行都落在 `Store` 的 P5 两表，基础授权判定
用 `StaticPolicy`，本模块只叠加"是否已有一份绑定当前 `(action, resource_digest)` 的批准"这一层。
高危动作集写死在 `HIGH_RISK_ACTIONS`：它不从 Mandate 字段、也不从任何外部文本推导，
`requires_approval=False` 关不掉它（规格 2.2"需要审批的动作不得先执行后补记录"）。
"""

from __future__ import annotations

from typing import Any

from stableagent.control.audit import AuditWriter
from stableagent.control.errors import ApprovalDecidedError, UnknownApprovalError
from stableagent.control.model import (
    Action,
    ApprovalRequest,
    ApprovalStatus,
    Decision,
    Resource,
    resource_digest,
)
from stableagent.control.policy import StaticPolicy
from stableagent.orchestration.errors import UnknownTicketError
from stableagent.orchestration.model import (
    AssignmentAction,
    AssignmentStatus,
    Clock,
    Effect,
    EffectKind,
    IdFactory,
    Mandate,
    Ticket,
    TicketAction,
    next_assignment_status,
    next_ticket_status,
)
from stableagent.orchestration.store import Store

# 高危集：对外部系统可见或难以撤销的写入。成员在这里写死而不是配置项，M2/M3 的接线点按同一集合判定。
HIGH_RISK_ACTIONS: frozenset[Action] = frozenset(
    {
        Action.PUSH_BRANCH,
        Action.CREATE_PULL_REQUEST,
        Action.COMMENT_ISSUE,
        Action.COMMENT_PULL_REQUEST,
        Action.REQUEST_REVIEW,
    }
)

# 与 P4 `OrchestrationService._ASSIGNMENT_ENDED_STATUSES` 同口径；那份常量是 service 私有的，
# 而约束 1 要求 service 一字不动，所以这里独立声明，由取消用例证明两处行为一致。
_ENDED_ASSIGNMENT_STATUSES = frozenset(
    {
        AssignmentStatus.ENDED,
        AssignmentStatus.FAILED,
        AssignmentStatus.CANCELLED,
        AssignmentStatus.INTERRUPTED,
    }
)


def _from_row(row: dict[str, Any]) -> ApprovalRequest:
    return ApprovalRequest(
        id=row["id"],
        ticket_id=row["ticket_id"],
        assignment_id=row["assignment_id"],
        action=Action(row["action"]),
        resource_digest=row["resource_digest"],
        reason=row["reason"],
        requested_at=row["requested_at"],
        status=ApprovalStatus(row["status"]),
        decided_by=row["decided_by"],
        decided_at=row["decided_at"],
    )


class ApprovalService:
    """审批行的唯一写入者：pending 键去重、终态不可改判、撤销只碰 pending。"""

    def __init__(self, store: Store, *, clock: Clock, ids: IdFactory) -> None:
        self._store = store
        self._clock = clock
        self._ids = ids

    def submit(
        self,
        ticket_id: str,
        action: Action,
        digest: str,
        reason: str,
        assignment_id: str | None = None,
    ) -> ApprovalRequest:
        """同一 `(ticket, action, digest)` 的 pending 行只有一条：重复申请返回既有行，不双开。"""
        for existing in self.for_ticket(ticket_id, status=ApprovalStatus.PENDING):
            if existing.action is action and existing.resource_digest == digest:
                return existing
        approval = ApprovalRequest(
            id=self._ids.new_id("ap"),
            ticket_id=ticket_id,
            assignment_id=assignment_id,
            action=action,
            resource_digest=digest,
            reason=reason,
            requested_at=self._clock.now_ms(),
        )
        self._store.insert_approval(
            id=approval.id,
            ticket_id=approval.ticket_id,
            assignment_id=approval.assignment_id,
            action=approval.action.value,
            resource_digest=approval.resource_digest,
            reason=approval.reason,
            requested_at=approval.requested_at,
            status=approval.status.value,
        )
        return approval

    def get(self, approval_id: str) -> ApprovalRequest | None:
        row = self._store.get_approval(approval_id)
        return _from_row(row) if row is not None else None

    def for_ticket(
        self, ticket_id: str, *, status: ApprovalStatus | None = None
    ) -> list[ApprovalRequest]:
        rows = self._store.list_approvals(
            ticket_id, status=None if status is None else status.value
        )
        return [_from_row(row) for row in rows]

    def decide(self, approval_id: str, approve: bool, by: str) -> ApprovalRequest:
        """pending → approved/denied 一次性翻转；终态再决定是错误，不做"改判"。"""
        current = self.get(approval_id)
        if current is None:
            raise UnknownApprovalError(approval_id)
        if current.status is not ApprovalStatus.PENDING:
            raise ApprovalDecidedError(approval_id, current.status.value)
        status = ApprovalStatus.APPROVED if approve else ApprovalStatus.DENIED
        now = self._clock.now_ms()
        with self._store.transaction():
            self._store.update_approval(
                approval_id, status=status.value, decided_by=by, decided_at=now
            )
            if not approve:
                self._fail_running_assignment(current.assignment_id, now)
        return current.model_copy(update={"status": status, "decided_by": by, "decided_at": now})

    def _fail_running_assignment(self, assignment_id: str | None, now: int) -> None:
        """B2：拒绝意味着这条执行线不能再换动作继续；只碰 RUNNING，终态行原样保留。"""
        if assignment_id is None:
            return
        assignment = self._store.get_assignment(assignment_id)
        if assignment is None or assignment.status is not AssignmentStatus.RUNNING:
            return
        self._store.update_assignment(
            assignment.model_copy(
                update={
                    "status": next_assignment_status(assignment.status, AssignmentAction.FAIL),
                    "ended_at": now,
                    "failure": "approval_denied",
                }
            )
        )

    def revoke_pending(self, ticket_id: str) -> int:
        """撤销只碰 pending 行：`decided_by` 留空（撤销不是人工裁决），`decided_at` 记终态时刻。"""
        now = self._clock.now_ms()
        pending = self.for_ticket(ticket_id, status=ApprovalStatus.PENDING)
        for approval in pending:
            self._store.update_approval(
                approval.id,
                status=ApprovalStatus.REVOKED.value,
                decided_by=None,
                decided_at=now,
            )
        return len(pending)


class ApprovalGate:
    """`policy.authorize` 之上的一道叠加：需要审批的动作，还要有一份全等批准才放行。

    计划签名里的 `store`/`clock` 未保留：审批行读取已经由 `ApprovalService` 承担，时间口径归
    `StaticPolicy` 的时钟，留两个没有消费点的依赖正是 ADR-05 批评 `permission_hook` 的那种
    "声明字段而非执行边界"。
    """

    def __init__(
        self, policy: StaticPolicy, approvals: ApprovalService, audit: AuditWriter
    ) -> None:
        self._policy = policy
        self._approvals = approvals
        self._audit = audit

    def check(
        self,
        mandate: Mandate,
        action: Action,
        resource: Resource,
        *,
        ticket_id: str | None = None,
    ) -> Decision:
        """每次 check 恰好一行 authorize 审计：allow 也留痕，deny 的 reason 是稳定码。"""
        authorization = self._policy.authorize(mandate, action, resource)
        if not authorization.allowed or not self._requires_approval(mandate, action):
            self._audit.authorized(mandate, action, resource, authorization, ticket_id=ticket_id)
            return authorization
        reason = self._approval_state(action, resource_digest(action, resource), ticket_id)
        verdict = Decision(
            allowed=reason == "approved", reason=reason, policy_version=mandate.policy_version
        )
        self._audit.authorized(mandate, action, resource, verdict, ticket_id=ticket_id)
        return verdict

    @staticmethod
    def _requires_approval(mandate: Mandate, action: Action) -> bool:
        return mandate.requires_approval or action in HIGH_RISK_ACTIONS

    def _approval_state(self, action: Action, digest: str, ticket_id: str | None) -> str:
        """denied 粘在同一 `(ticket, action)` 上，先于批准匹配判定：换资源也不给自愈通道。"""
        if ticket_id is None:
            return "approval_context_missing"
        same_action = [
            item for item in self._approvals.for_ticket(ticket_id) if item.action is action
        ]
        if any(item.status is ApprovalStatus.DENIED for item in same_action):
            return "approval_denied"
        approved = [item for item in same_action if item.status is ApprovalStatus.APPROVED]
        if any(item.resource_digest == digest for item in approved):
            return "approved"
        return "approval_stale" if approved else "approval_pending"


class GuardedTicketOps:
    """守卫式 Ticket 操作：与 P4 `cancel_ticket` 同形，另在同一事务内撤销 pending 审批（B3）。"""

    def __init__(
        self, store: Store, approvals: ApprovalService, *, clock: Clock, ids: IdFactory
    ) -> None:
        self._store = store
        self._approvals = approvals
        self._clock = clock
        self._ids = ids

    def cancel_ticket(self, ticket_id: str, reason: str | None = None) -> Ticket:
        """取消转换 + 未终态 Assignment 的 cancel effect + 撤销 pending，全在一个事务里。

        事务体内只有同步的库写与纯函数：没有 await、也没有外部 I/O（L3），所以任一步失败
        三件事一起回滚——不会出现"票已取消、批准还在挂着"的中间态。
        """
        with self._store.transaction():
            ticket = self._store.get_ticket(ticket_id)
            if ticket is None:
                raise UnknownTicketError(ticket_id)
            now = self._clock.now_ms()
            updated = ticket.model_copy(
                update={
                    "status": next_ticket_status(ticket.status, TicketAction.CANCEL),
                    "ended_at": now,
                }
            )
            self._store.update_ticket(updated)
            for assignment in self._store.list_assignments(ticket_id):
                if assignment.status in _ENDED_ASSIGNMENT_STATUSES:
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
            self._approvals.revoke_pending(ticket_id)
            return updated
