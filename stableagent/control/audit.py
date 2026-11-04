"""审计写入（步骤 1.6 / A3 / L5）：每一次授权判定恰好一行 `authorization_audit`。

写入只经 `orchestration.Store` 的仓储方法，本模块不拼 SQL、不开事务：每条 INSERT 是单
语句，sqlite3 自动提交即为原子写，调用方已在更大的 `store.transaction()` 内时也不会重入
（L3 的"事务体内禁 await"因此天然满足）。`policy_version` 写**决策当刻**生效的策略版本
（不是被判定 Mandate 的自述版本），这样版本漂移在审计上是可见的两行差异而不是一致假象。
`reason` 只存稳定码：判定码来自 `StaticPolicy`，人工裁决码由 `control.approval` 给定。
"""

from __future__ import annotations

from stableagent.control.model import (
    Action,
    Admission,
    ApprovalRequest,
    Decision,
    Resource,
    resource_digest,
)
from stableagent.control.policy import StaticPolicy
from stableagent.orchestration.model import Clock, IdFactory, Mandate
from stableagent.orchestration.store import Store

ALLOW = "allow"
DENY = "deny"


def _verdict(decision: Decision) -> str:
    return ALLOW if decision.allowed else DENY


class AuditWriter:
    def __init__(self, policy: StaticPolicy, store: Store, *, clock: Clock, ids: IdFactory) -> None:
        self._policy = policy
        self._store = store
        self._clock = clock
        self._ids = ids

    def admitted(self, admission: Admission, *, ticket_id: str | None = None) -> None:
        """kind=admit：受理门的 allow/deny，没有 Mandate 可引用，槽位留空而非伪造。"""
        self._write(
            "admit",
            ALLOW if admission.admitted else DENY,
            admission.reason_code,
            ticket_id=ticket_id,
        )

    def authorized(
        self,
        mandate: Mandate,
        action: Action,
        resource: Resource,
        decision: Decision,
        *,
        ticket_id: str | None = None,
        assignment_id: str | None = None,
    ) -> None:
        self._write(
            "authorize",
            _verdict(decision),
            decision.reason,
            ticket_id=ticket_id,
            assignment_id=assignment_id,
            mandate_id=mandate.id,
            action=action.value,
            digest=resource_digest(action, resource),
        )

    def revalidated(
        self,
        mandate: Mandate,
        when: str,
        decision: Decision,
        *,
        ticket_id: str | None = None,
        assignment_id: str | None = None,
    ) -> None:
        """kind=revalidate：action 槽记复检时机名（列名钉死于 ADR-05 决策 3，不新增列）。"""
        self._write(
            "revalidate",
            _verdict(decision),
            decision.reason,
            ticket_id=ticket_id,
            assignment_id=assignment_id,
            mandate_id=mandate.id,
            action=when,
        )

    def decided(self, approval: ApprovalRequest, decision: Decision) -> None:
        """kind=approve：人工裁决先落这一行，动作才允许执行（B4 的先记录后执行）。"""
        self._write(
            "approve",
            _verdict(decision),
            decision.reason,
            ticket_id=approval.ticket_id,
            assignment_id=approval.assignment_id,
            action=approval.action.value,
            digest=approval.resource_digest,
        )

    def _write(
        self,
        kind: str,
        decision: str,
        reason: str,
        *,
        ticket_id: str | None = None,
        assignment_id: str | None = None,
        mandate_id: str | None = None,
        action: str | None = None,
        digest: str | None = None,
    ) -> None:
        self._store.insert_audit(
            id=self._ids.new_id("aud"),
            kind=kind,
            policy_version=self._policy.policy_version,
            decision=decision,
            reason=reason,
            created_at=self._clock.now_ms(),
            ticket_id=ticket_id,
            assignment_id=assignment_id,
            mandate_id=mandate_id,
            action=action,
            resource_digest=digest,
        )
