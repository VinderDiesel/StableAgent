"""Router 的运维命令层：ticket 域命令，全部检查 operator 与状态（规格 WP-6.3 第 4~6 条）。

方向锁：service → router → supervisor.store → orchestration；不 import integrations。
receive / reevaluate 留在 RouterService；命令层只做 Ticket / Subscription 的受控变更。
"""

from __future__ import annotations

from stableagent.control.audit import AuditWriter
from stableagent.control.policy import StaticPolicy
from stableagent.orchestration.model import (
    TERMINAL_TICKET_STATUSES,
    AssignmentStatus,
    Clock,
    Effect,
    EffectKind,
    IdFactory,
    Ticket,
    TicketAction,
    TicketStatus,
    next_ticket_status,
)
from stableagent.supervisor.model import (
    Decision,
    DecisionOutcome,
    DecisionStrength,
    Subscription,
    SubscriptionMode,
    Trigger,
    payload_digest,
)
from stableagent.supervisor.router import (
    Reception,
    RouterNotFoundError,
    RouterPermissionError,
    RouterService,
    RouterStateError,
    ensure_subscription,
    latest_trigger,
    record_decision,
)
from stableagent.supervisor.store import SupervisorStore

_LIVE_ASSIGNMENT_STATUSES = (
    AssignmentStatus.PENDING,
    AssignmentStatus.RUNNING,
    AssignmentStatus.INTERRUPTED,
)


class SupervisorService:
    def __init__(
        self,
        store: SupervisorStore,
        policy: StaticPolicy,
        router: RouterService,
        *,
        clock: Clock,
        ids: IdFactory,
        audit: AuditWriter | None = None,
    ) -> None:
        self._store = store
        self._policy = policy
        self._router = router
        self._clock = clock
        self._ids = ids
        self._audit = audit

    @property
    def router(self) -> RouterService:
        return self._router

    def receive(self, trigger: Trigger) -> Reception:
        return self._router.receive(trigger)

    def reevaluate(self, trigger_id: str, *, actor: str) -> Decision:
        self._require_operator(actor)
        reception = self._router.reevaluate(trigger_id)
        if not reception.decision_ids:
            raise RouterStateError("reevaluate 未产生 Decision")
        decision = self._store.get_decision(reception.decision_ids[0])
        if decision is None:  # pragma: no cover - 刚写入的行必可读
            raise RouterStateError("Decision 缺失")
        return decision

    def attach(self, ticket_id: str, *, thread_key: str, actor: str) -> Subscription:
        """把一个相关 Thread 作为 context 订阅接上；已有 live 订阅则原样返回。"""
        self._require_operator(actor)
        with self._store.transaction():
            ticket = self._require_live_ticket(ticket_id)
            self._require_thread(thread_key)
            live = self._store.list_subscriptions(
                ticket_id=ticket.id, thread_key=thread_key, live_only=True
            )
            if live:
                return live[0]
            return ensure_subscription(
                self._store,
                ids=self._ids,
                ticket_id=ticket.id,
                thread_key=thread_key,
                mode=SubscriptionMode.CONTEXT,
                created_at=self._clock.now_ms(),
            )

    def subscribe(self, ticket_id: str, *, trigger_id: str, actor: str) -> Subscription:
        trigger = self._require_trigger(trigger_id)
        if not trigger.thread_keys:
            raise RouterStateError("trigger 没有 thread key")
        return self.attach(ticket_id, thread_key=trigger.thread_keys[0], actor=actor)

    def unsubscribe(self, ticket_id: str, *, thread_key: str, actor: str) -> int:
        self._require_operator(actor)
        with self._store.transaction():
            self._require_ticket(ticket_id)
            ended = 0
            for subscription in self._store.list_subscriptions(
                ticket_id=ticket_id, thread_key=thread_key, live_only=True
            ):
                ended += self._store.end_subscription(
                    subscription.id, ended_at=self._clock.now_ms()
                )
            return ended

    def stop(self, ticket_id: str, *, actor: str, reason: str | None = None) -> Ticket:
        """人工停：取消未结束 Ticket、入队 cancel effect、只结束 context 订阅。

        own 订阅故意保留：被 stop 的 Ticket 之后收到的明确后续消息由 Router 记 STOP 并
        丢弃，不会因为订阅消失而被当成新任务；人工 resume 也拒绝 CANCELLED。
        """
        self._require_operator(actor)
        with self._store.transaction():
            ticket = self._require_ticket(ticket_id)
            now = self._clock.now_ms()
            if ticket.status is TicketStatus.CANCELLED:
                return ticket
            updated = ticket
            if ticket.status not in TERMINAL_TICKET_STATUSES:
                updated = ticket.model_copy(
                    update={
                        "status": next_ticket_status(ticket.status, TicketAction.CANCEL),
                        "ended_at": now,
                    }
                )
                self._store.update_ticket(updated)
                for assignment in self._store.list_assignments(ticket_id):
                    if assignment.status not in _LIVE_ASSIGNMENT_STATUSES:
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
            for subscription in self._store.list_subscriptions(ticket_id=ticket_id, live_only=True):
                if subscription.mode is SubscriptionMode.CONTEXT:
                    self._store.end_subscription(subscription.id, ended_at=now)
            record_decision(
                self._store,
                ids=self._ids,
                created_at=now,
                trigger_ids=self._decision_triggers(ticket_id),
                outcome=DecisionOutcome.STOP,
                strength=DecisionStrength.EXPLICIT,
                reason_codes=["manual_stop"],
                policy_version=self._policy.policy_version,
                request_digest=payload_digest({"command": "stop", "ticket": ticket_id}),
            )
            return updated

    # ------------------------------------------------------------- 内部
    def _require_operator(self, actor: str) -> None:
        allowed = {name.casefold() for name in self._router.config.operators}
        if actor.casefold() not in allowed:
            raise RouterPermissionError(f"actor 不是 operator：{actor}")

    def _require_ticket(self, ticket_id: str) -> Ticket:
        ticket = self._store.get_ticket(ticket_id)
        if ticket is None:
            raise RouterNotFoundError(f"ticket 不存在：{ticket_id}")
        return ticket

    def _require_live_ticket(self, ticket_id: str) -> Ticket:
        ticket = self._require_ticket(ticket_id)
        if ticket.status in TERMINAL_TICKET_STATUSES:
            raise RouterStateError(f"ticket 已结束：{ticket.status.value}")
        return ticket

    def _require_thread(self, thread_key: str) -> None:
        if self._store.get_thread(thread_key) is None:
            raise RouterNotFoundError(f"thread 不存在：{thread_key}")

    def _require_trigger(self, trigger_id: str) -> Trigger:
        trigger = self._store.get_trigger(trigger_id)
        if trigger is None:
            raise RouterNotFoundError(f"trigger 不存在：{trigger_id}")
        return trigger

    def _decision_triggers(self, ticket_id: str) -> list[str]:
        own = [
            subscription.thread_key
            for subscription in self._store.list_subscriptions(ticket_id=ticket_id)
            if subscription.mode is SubscriptionMode.OWN
        ]
        if not own:
            return []
        trigger = latest_trigger(self._store, own[0])
        return [trigger.id] if trigger is not None else []
