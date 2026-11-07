"""GitHub 显式请求的入口：四道受理门 + 稳定 ID 查重 + 五件同一事务原子写。

外部文本只进请求摘要与 `Ticket.task`/`TicketInput.payload`，不参与任何判定（L4）。
方向锁：integrations 可以 import control 与 orchestration；事务体内零 `await`（L3）——
建票、入队、审计的顺序就是崩溃后能看到的事实顺序。
"""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import BaseModel

from stableagent.control.audit import AuditWriter
from stableagent.control.model import Action, Admission, ExplicitRequest
from stableagent.control.policy import StaticPolicy
from stableagent.orchestration.model import (
    Assignment,
    Clock,
    Effect,
    EffectKind,
    IdFactory,
    Ticket,
    TicketInput,
    TicketStatus,
)
from stableagent.orchestration.store import Store


class ExplicitGitHubRequest(BaseModel):
    """GitHub 侧的请求形状；`url`/`received_at` 只做轨迹留存，没有授权含义。"""

    repository: str
    url: str
    actor: str
    text: str
    mentioned: bool
    received_at: int
    stable_id: str

    def to_explicit_request(self) -> ExplicitRequest:
        """只搬 5 个判定字段：把链接与时间戳搬过去，只是无谓地加宽外部输入面。"""
        return ExplicitRequest(
            repository=self.repository,
            requester=self.actor,
            mentioned=self.mentioned,
            stable_id=self.stable_id,
            text=self.text,
        )


class RequestOutcome(BaseModel):
    admission: Admission
    ticket_id: str | None = None
    duplicate: bool = False


class GitHubIntake:
    """唯一的 GitHub 入口：先受理并审计（事务外），再在一个事务里查重并落五件事实。"""

    def __init__(
        self,
        store: Store,
        policy: StaticPolicy,
        audit: AuditWriter,
        *,
        clock: Clock,
        ids: IdFactory,
    ) -> None:
        self._store = store
        self._policy = policy
        self._audit = audit
        self._clock = clock
        self._ids = ids

    def submit(
        self,
        request: ExplicitGitHubRequest,
        *,
        actions: Iterable[Action | str],
        allowed_paths: Iterable[str] = (),
        allowed_commands: Iterable[str] = (),
        allowed_mcp: Iterable[str] = (),
        network_profile: str | None = None,
        base_ref: str | None = None,
        requires_approval: bool = False,
    ) -> RequestOutcome:
        admission = self._policy.admit_explicit(request.to_explicit_request())
        # 审计在事务外：受理结论已落定，建票回滚不该抹掉"这次请求来过"（B4 口径）
        self._audit.admitted(admission)
        if not admission.admitted:
            return RequestOutcome(admission=admission)
        with self._store.transaction():
            existing = self._store.get_ticket_by_mandate_scope(
                admission.stable_id, origin_kind="github"
            )
            if existing is not None:
                return RequestOutcome(admission=admission, ticket_id=existing.id, duplicate=True)
            ticket = self._create(
                admission,
                request,
                actions=actions,
                allowed_paths=allowed_paths,
                allowed_commands=allowed_commands,
                allowed_mcp=allowed_mcp,
                network_profile=network_profile,
                base_ref=base_ref,
                requires_approval=requires_approval,
            )
        return RequestOutcome(admission=admission, ticket_id=ticket.id)

    def _create(
        self,
        admission: Admission,
        request: ExplicitGitHubRequest,
        *,
        actions: Iterable[Action | str],
        allowed_paths: Iterable[str],
        allowed_commands: Iterable[str],
        allowed_mcp: Iterable[str],
        network_profile: str | None,
        base_ref: str | None,
        requires_approval: bool,
    ) -> Ticket:
        """五件事实：Mandate、Ticket、首个 Input、首个 Assignment、launch effect。

        Ticket 直接落 QUEUED：受理即"可执行"，调度只看 queued（与 P4 `queue_ticket` 的
        终态一致）；若留 OPEN，launch 之后第一个状态转换就会非法。
        """
        mandate = self._policy.create_mandate(
            admission,
            actions=actions,
            allowed_paths=allowed_paths,
            allowed_commands=allowed_commands,
            allowed_mcp=allowed_mcp,
            network_profile=network_profile,
            base_ref=base_ref,
            requires_approval=requires_approval,
        )
        now = self._clock.now_ms()
        ticket = Ticket(
            id=self._ids.new_id("tk"),
            title=request.text[:40],
            task=request.text,
            created_at=now,
            status=TicketStatus.QUEUED,
            queued_at=now,
            mandate_id=mandate.id,
        )
        assignment = Assignment(id=self._ids.new_id("as"), ticket_id=ticket.id, attempt=1)
        self._store.insert_mandate(mandate)
        self._store.insert_ticket(ticket)
        self._store.insert_input(
            TicketInput(
                id=self._ids.new_id("in"),
                ticket_id=ticket.id,
                sequence=1,
                kind="task",
                payload=request.text,
                created_at=now,
            )
        )
        self._store.insert_assignment(assignment)
        self._store.enqueue_effect(
            Effect(
                id=self._ids.new_id("ef"),
                kind=EffectKind.LAUNCH_ASSIGNMENT,
                idempotency_key=f"launch:{assignment.id}:1",
                payload={"assignment_id": assignment.id, "ticket_id": ticket.id},
                due_at=now,
            )
        )
        return ticket
