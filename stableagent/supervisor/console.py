"""CLI 监管控制台：门禁、--confirm 与渲染；业务规则在既有服务层（规格 WP-6.5）。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from stableagent.control.approval import ApprovalService
from stableagent.control.audit import AuditWriter
from stableagent.control.errors import ApprovalDecidedError, UnknownApprovalError
from stableagent.control.model import Action, ApprovalRequest, ApprovalStatus
from stableagent.control.policy import P5_POLICY_VERSION, StaticPolicy, StaticPolicyConfig
from stableagent.orchestration.model import (
    Assignment,
    Clock,
    Effect,
    IdFactory,
    Ticket,
    TicketStatus,
)
from stableagent.orchestration.service import OrchestrationService
from stableagent.supervisor.assessor import ObserveOnlyAssessor
from stableagent.supervisor.model import Decision, RuntimeControl
from stableagent.supervisor.notifications import (
    NotificationConfig,
    NotificationDraft,
    NotificationDrafter,
    notification_effect,
)
from stableagent.supervisor.router import RouterConfig, RouterService, require_operator
from stableagent.supervisor.runtime import RuntimeStatus, read_runtime_status
from stableagent.supervisor.service import SupervisorService
from stableagent.supervisor.store import SupervisorStore
from stableagent.supervisor.views import (
    StatusSections,
    SupervisorViews,
    TicketDetail,
    TimelineEvent,
)


class CliError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.detail = message


class ConsoleConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    repository: str
    operators: frozenset[str] = frozenset()
    actors: frozenset[str] = frozenset()
    actions: tuple[str, ...] = ()
    allowed_paths: tuple[str, ...] = ()
    allowed_commands: tuple[str, ...] = ()
    allowed_mcp: tuple[str, ...] = ()
    network_profile: str | None = None
    base_ref: str | None = None
    requires_approval: bool = False
    default_validity_ms: int = Field(default=86_400_000, ge=1)
    policy_version: str = P5_POLICY_VERSION
    notifications: NotificationConfig | None = None


class StatusView(BaseModel):
    model_config = ConfigDict(frozen=True)

    sections: StatusSections
    runtime: RuntimeStatus


class Console:
    def __init__(
        self,
        store: SupervisorStore,
        *,
        clock: Clock,
        ids: IdFactory,
        config: ConsoleConfig,
        audit: AuditWriter | None = None,
    ) -> None:
        self._store = store
        self._clock = clock
        self._ids = ids
        self._config = config
        try:
            grantable = frozenset(Action(item) for item in config.actions)
        except ValueError as error:
            raise CliError(1, f"未知 action：{error}") from error
        policy = StaticPolicy(
            StaticPolicyConfig(
                repository=config.repository,
                actors=config.actors,
                grantable_actions=grantable,
                allowed_commands=list(config.allowed_commands),
                allowed_mcp=list(config.allowed_mcp),
                default_validity_ms=config.default_validity_ms,
                policy_version=config.policy_version,
            ),
            clock=clock,
            ids=ids,
        )
        router = RouterService(
            store,
            policy,
            ObserveOnlyAssessor(),
            RouterConfig(
                repository=config.repository,
                operators=config.operators,
                actions=config.actions,
                allowed_paths=config.allowed_paths,
                allowed_commands=config.allowed_commands,
                allowed_mcp=config.allowed_mcp,
                network_profile=config.network_profile,
                base_ref=config.base_ref,
                requires_approval=config.requires_approval,
            ),
            clock=clock,
            ids=ids,
            audit=audit,
        )
        self._approvals = ApprovalService(store, clock=clock, ids=ids)
        self._service = SupervisorService(
            store,
            policy,
            router,
            clock=clock,
            ids=ids,
            audit=audit,
            approvals=self._approvals,
        )
        self._orchestration = OrchestrationService(store, clock=clock, ids=ids)
        self._views = SupervisorViews(store, clock=clock)
        self._drafter = (
            None
            if config.notifications is None
            else NotificationDrafter(
                store,
                self._approvals,
                config.notifications,
                repository=config.repository,
                clock=clock,
            )
        )

    def status(self) -> StatusView:
        return StatusView(
            sections=self._views.status_sections(),
            runtime=read_runtime_status(self._store, self._clock),
        )

    def ticket(self, ticket_id: str) -> TicketDetail:
        return self._views.ticket_detail(ticket_id)

    def thread(self, thread_key: str) -> list[TimelineEvent]:
        return self._views.thread_timeline(thread_key)

    def approve(self, approval_id: str, *, actor: str, confirm: str) -> ApprovalRequest:
        self._require_operator(actor)
        self._confirm(confirm, approval_id)
        approval = self._existing_approval(approval_id)
        effect = None
        if approval.reason.startswith("notification:"):
            draft = self._build_notification(approval.ticket_id)
            if draft is None or draft.digest != approval.resource_digest:
                raise CliError(3, "通知草稿已变化，请重新 status 后再批准")
            effect = notification_effect(
                draft, effect_id=self._ids.new_id("ef"), now=self._clock.now_ms()
            )
        return self._approvals.decide(approval_id, True, actor, effect=effect)

    def deny(self, approval_id: str, *, actor: str, confirm: str) -> ApprovalRequest:
        self._require_operator(actor)
        self._confirm(confirm, approval_id)
        self._existing_approval(approval_id)
        return self._approvals.decide(approval_id, False, actor)

    def stop(
        self,
        ticket_id: str,
        *,
        actor: str,
        confirm: str,
        reason: str | None = None,
    ) -> Ticket:
        self._require_operator(actor)
        self._confirm(confirm, ticket_id)
        return self._service.stop(ticket_id, actor=actor, reason=reason)

    def retry(self, target_id: str, *, actor: str, confirm: str) -> Effect | Assignment:
        self._require_operator(actor)
        self._confirm(confirm, target_id)
        if self._store.get_effect(target_id) is not None:
            return self._service.retry_effect(target_id, actor=actor)
        ticket = self._store.get_ticket(target_id)
        if ticket is None:
            raise CliError(1, f"未知 effect 或 ticket：{target_id}")
        if ticket.status is not TicketStatus.FAILED:
            raise CliError(1, f"ticket 不是 FAILED：{ticket.status.value}")
        return self._orchestration.retry_ticket(target_id)

    def reevaluate(self, trigger_id: str, *, actor: str, confirm: str) -> Decision:
        self._require_operator(actor)
        self._confirm(confirm, trigger_id)
        return self._service.reevaluate(trigger_id, actor=actor)

    def pause(self, *, actor: str, confirm: str, reason: str | None = None) -> RuntimeControl:
        self._require_operator(actor)
        self._confirm(confirm, "pause")
        return self._store.set_runtime_paused(
            True, actor=actor, now=self._clock.now_ms(), reason=reason
        )

    def resume(self, *, actor: str, confirm: str, reason: str | None = None) -> RuntimeControl:
        self._require_operator(actor)
        self._confirm(confirm, "resume")
        return self._store.set_runtime_paused(
            False, actor=actor, now=self._clock.now_ms(), reason=reason
        )

    def _require_operator(self, actor: str) -> None:
        require_operator(self._config.operators, actor)

    @staticmethod
    def _confirm(actual: str, expected: str) -> None:
        if actual != expected:
            raise CliError(2, f"--confirm 必须等于 {expected}")

    def _existing_approval(self, approval_id: str) -> ApprovalRequest:
        approval = self._approvals.get(approval_id)
        if approval is None:
            raise UnknownApprovalError(approval_id)
        if approval.status is not ApprovalStatus.PENDING:
            raise ApprovalDecidedError(approval_id, approval.status.value)
        return approval

    def _build_notification(self, ticket_id: str) -> NotificationDraft | None:
        if self._drafter is None:
            return None
        return self._drafter.build(ticket_id)


def render_status(view: StatusView) -> str:
    runtime = view.runtime
    lines = [
        f"runtime started={runtime.started} paused={runtime.paused} owner={runtime.owner or '-'}",
        "queue"
        f" queued_tickets={view.sections.queue.queued_tickets}"
        f" running_assignments={view.sections.queue.running_assignments}"
        f" due_effects={view.sections.queue.due_effects}"
        f" failed_effects={view.sections.queue.failed_effects}"
        f" pending_approvals={view.sections.queue.pending_approvals}",
    ]
    for cursor in view.sections.source_health:
        lines.append(
            f"source {cursor.source} health={cursor.health.value}"
            f" cursor={cursor.cursor or '-'} failures={cursor.consecutive_failures}"
        )
    if not view.sections.attention:
        lines.append("attention none")
    for item in view.sections.attention:
        lines.append(
            f"attention {item.priority} {item.source} target={item.target_id}"
            f" ticket={item.ticket_id or '-'} — {item.detail}"
            f" [{'/'.join(item.actions)}]"
        )
        if item.ticket_link:
            lines.append(f"  link {item.ticket_link}")
    return "\n".join(lines)


def render_ticket(detail: TicketDetail) -> str:
    ticket = detail.ticket
    lines = [
        f"ticket {ticket.id} status={ticket.status.value} title={ticket.title}",
        f"mandate {detail.mandate_id}",
    ]
    if detail.ticket_link:
        lines.append(f"link {detail.ticket_link}")
    for assignment in detail.assignments:
        lines.append(
            f"assignment {assignment.id} attempt={assignment.attempt}"
            f" status={assignment.status.value} failure={assignment.failure or '-'}"
        )
    for subscription in detail.subscriptions:
        lines.append(
            f"subscription {subscription.id} thread={subscription.thread_key}"
            f" mode={subscription.mode.value} ended_at={subscription.ended_at or '-'}"
        )
    for event in detail.timeline:
        lines.append(f"timeline {event.at} {event.kind} {event.object_id or '-'} {event.summary}")
    return "\n".join(lines)


def render_thread(events: list[TimelineEvent]) -> str:
    if not events:
        return "thread empty"
    return "\n".join(
        f"timeline {event.at} {event.kind} {event.object_id or '-'} {event.summary}"
        for event in events
    )


def render_approval(approval: ApprovalRequest, *, approved: bool) -> str:
    verb = "approved" if approved else "denied"
    return f"{verb} {approval.id} ticket={approval.ticket_id}"


def render_retry(result: Effect | Assignment) -> str:
    if isinstance(result, Effect):
        return f"retried effect {result.id} state={result.state.value}"
    return f"retried ticket {result.ticket_id} assignment={result.id}"


def render_decision(decision: Decision) -> str:
    return f"reevaluated {decision.id} outcome={decision.outcome.value}"


def render_control(control: RuntimeControl) -> str:
    return "paused" if control.paused else "resumed"
