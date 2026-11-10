"""Router Intake：Trigger 进入领域的唯一命令（规格 WP-6.3、ADR-06 决策 3/4/6/7/8）。

方向锁：只 import supervisor 记录与 Store、P5 Policy 与 orchestration 记录；外部形态由
Source 标准化，Router 不解析 GitHub 原始字段。事务：`receive` 只开一个 BEGIN IMMEDIATE，
体内无 await（L3）；任一步失败整体回滚。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from stableagent.control.audit import AuditWriter
from stableagent.control.model import Admission, ExplicitRequest
from stableagent.control.policy import StaticPolicy
from stableagent.control.policy import request_digest as policy_request_digest
from stableagent.observability.emitter import DomainEventEmitter
from stableagent.observability.events import DomainEventKind
from stableagent.orchestration.model import (
    TERMINAL_TICKET_STATUSES,
    Assignment,
    Clock,
    Effect,
    EffectKind,
    IdFactory,
    Ticket,
    TicketInput,
    TicketStatus,
)
from stableagent.supervisor.assessor import AssessmentContext, CandidateAssessor
from stableagent.supervisor.auto import AutoGate
from stableagent.supervisor.model import (
    AutonomyMode,
    Decision,
    DecisionOutcome,
    DecisionStrength,
    Subscription,
    SubscriptionMode,
    Thread,
    Trigger,
    TriggerKind,
    canonical_json,
)
from stableagent.supervisor.store import SupervisorStore

_ALLOWED_MODES = (AutonomyMode.OFF, AutonomyMode.OBSERVE, AutonomyMode.AUTO)


class RouterError(Exception):
    code = "router.error"

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.detail = message
        self.code = code


class RouterModeError(RouterError):
    def __init__(self, message: str) -> None:
        super().__init__("router.mode", message)


class RouterPayloadError(RouterError):
    def __init__(self, message: str) -> None:
        super().__init__("router.payload", message)


class RouterNotFoundError(RouterError):
    def __init__(self, message: str) -> None:
        super().__init__("router.not_found", message)


class RouterPermissionError(RouterError):
    def __init__(self, message: str) -> None:
        super().__init__("router.permission", message)


class RouterStateError(RouterError):
    def __init__(self, message: str) -> None:
        super().__init__("router.state", message)


class RouterConfig(BaseModel):
    """operator 写定的单仓库配置；actions 只来自这里，不来自外部文本（L4）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    repository: str
    mode: AutonomyMode = AutonomyMode.OBSERVE
    operators: frozenset[str] = frozenset()
    actions: tuple[str, ...] = ()
    allowed_paths: tuple[str, ...] = ()
    allowed_commands: tuple[str, ...] = ()
    allowed_mcp: tuple[str, ...] = ()
    network_profile: str | None = None
    base_ref: str | None = None
    requires_approval: bool = False


def require_operator(operators: frozenset[str], actor: str) -> None:
    allowed = {name.casefold() for name in operators}
    if actor.casefold() not in allowed:
        raise RouterPermissionError(f"actor 不是 operator：{actor}")


class RequestSignal(BaseModel):
    """Source 标准化后的显式请求；text 只进 digest 与 Input，不参与判定（L4）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str
    mentioned: bool
    stable_id: str
    url: str | None = None

    @classmethod
    def from_trigger(cls, trigger: Trigger) -> RequestSignal | None:
        block = trigger.payload.get("request")
        if block is None:
            return None
        if not isinstance(block, dict):
            raise RouterPayloadError("request 块不是对象")
        text = block.get("text")
        mentioned = block.get("mentioned")
        if not isinstance(text, str) or not isinstance(mentioned, bool):
            raise RouterPayloadError("request 块缺少 text/mentioned")
        url = block.get("url")
        if url is not None and not isinstance(url, str):
            raise RouterPayloadError("request.url 不是字符串")
        return cls(text=text, mentioned=mentioned, stable_id=trigger.external_id, url=url)


class Reception(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    trigger_id: str
    duplicate: bool = False
    decision_ids: list[str] = Field(default_factory=list)
    ticket_id: str | None = None
    input_ids: list[str] = Field(default_factory=list)
    fanned_out: int = 0


def record_decision(
    store: SupervisorStore,
    *,
    ids: IdFactory,
    created_at: int,
    trigger_ids: list[str],
    outcome: DecisionOutcome,
    strength: DecisionStrength,
    reason_codes: list[str],
    policy_version: str,
    request_digest: str,
    mandate_id: str | None = None,
) -> Decision:
    decision = Decision(
        id=ids.new_id("dc"),
        trigger_ids=list(trigger_ids),
        outcome=outcome,
        strength=strength,
        reason_codes=list(reason_codes),
        policy_version=policy_version,
        mandate_id=mandate_id,
        request_digest=request_digest,
        attempt=store.next_decision_attempt(request_digest),
        created_at=created_at,
    )
    store.insert_decision(decision)
    return decision


def ensure_subscription(
    store: SupervisorStore,
    *,
    ids: IdFactory,
    ticket_id: str,
    thread_key: str,
    mode: SubscriptionMode,
    created_at: int,
) -> Subscription:
    for subscription in store.list_subscriptions(
        ticket_id=ticket_id, thread_key=thread_key, live_only=True
    ):
        if subscription.mode is mode:
            return subscription
    subscription = Subscription(
        id=ids.new_id("sb"),
        ticket_id=ticket_id,
        thread_key=thread_key,
        mode=mode,
        created_at=created_at,
    )
    store.insert_subscription(subscription)
    return subscription


def latest_trigger(store: SupervisorStore, thread_key: str) -> Trigger | None:
    triggers = store.list_triggers(thread_key=thread_key, limit=1_000)
    return triggers[-1] if triggers else None


def _input_text(trigger: Trigger, signal: RequestSignal | None) -> str:
    if signal is not None:
        return signal.text
    return canonical_json(trigger.payload)


class RouterService:
    def __init__(
        self,
        store: SupervisorStore,
        policy: StaticPolicy,
        assessor: CandidateAssessor,
        config: RouterConfig,
        *,
        clock: Clock,
        ids: IdFactory,
        audit: AuditWriter | None = None,
        emitter: DomainEventEmitter | None = None,
        auto_gate: AutoGate | None = None,
    ) -> None:
        if config.mode not in _ALLOWED_MODES:
            raise RouterModeError(f"{config.mode.value} 未启用")
        self._store = store
        self._policy = policy
        self._assessor = assessor
        self._config = config
        self._clock = clock
        self._ids = ids
        self._audit = audit
        self._emitter = emitter
        self._auto_gate = auto_gate

    @property
    def config(self) -> RouterConfig:
        return self._config

    def receive(self, trigger: Trigger) -> Reception:
        signal, signal_error = self._read_signal(trigger)
        admission = self._admit(trigger, signal)
        with self._store.transaction():
            reception = self._route(
                trigger,
                signal=signal,
                signal_error=signal_error,
                admission=admission,
                reevaluate=False,
            )
        self._emit_reception(reception)
        return reception

    def reevaluate(self, trigger_id: str) -> Reception:
        trigger = self._store.get_trigger(trigger_id)
        if trigger is None:
            raise RouterNotFoundError(f"trigger 不存在：{trigger_id}")
        signal, signal_error = self._read_signal(trigger)
        admission = self._admit(trigger, signal)
        with self._store.transaction():
            reception = self._route(
                trigger,
                signal=signal,
                signal_error=signal_error,
                admission=admission,
                reevaluate=True,
            )
        self._emit_reception(reception)
        return reception

    def _emit_reception(self, reception: Reception) -> None:
        if self._emitter is None:
            return
        outcome = "duplicate" if reception.duplicate else "ok"
        self._emitter.emit_safe(
            DomainEventKind.TRIGGER_RECEIVED,
            outcome=outcome,
            trigger_id=reception.trigger_id,
            ticket_id=reception.ticket_id,
            attrs={
                "decision_count": len(reception.decision_ids),
                "fanned_out": reception.fanned_out,
            },
        )
        for decision_id in reception.decision_ids:
            self._emitter.emit_safe(
                DomainEventKind.DECISION_RECORDED,
                outcome=outcome,
                trigger_id=reception.trigger_id,
                ticket_id=reception.ticket_id,
                decision_id=decision_id,
            )

    # ------------------------------------------------------------------ 路由
    def _route(
        self,
        trigger: Trigger,
        *,
        signal: RequestSignal | None,
        signal_error: str | None,
        admission: Admission | None,
        reevaluate: bool = False,
    ) -> Reception:
        if not reevaluate:
            if self._already_decided(trigger.id):
                return Reception(trigger_id=trigger.id, duplicate=True)
            self._store.insert_trigger(trigger)
        now = self._clock.now_ms()
        input_ids = [] if reevaluate else self._fanout(trigger, now, signal)
        threads = [
            thread
            for key in trigger.thread_keys
            if (thread := self._store.get_thread(key)) is not None
        ]
        primary = threads[0] if threads else None
        if signal_error is not None:
            decision = self._ignore(
                trigger,
                now,
                signal_error,
                DecisionStrength.NOT_ADMITTED,
                digest=trigger.digest,
            )
            return self._reception(trigger, decision, input_ids=input_ids)
        if admission is not None and signal is not None:
            return self._route_explicit(trigger, signal, admission, primary, now, input_ids)
        return self._route_inferred(trigger, threads, now, input_ids, reevaluate=reevaluate)

    def _route_explicit(
        self,
        trigger: Trigger,
        signal: RequestSignal,
        admission: Admission,
        primary: Thread | None,
        now: int,
        input_ids: list[str],
    ) -> Reception:
        if not admission.admitted:
            decision = self._ignore(
                trigger,
                now,
                admission.reason_code,
                DecisionStrength.NOT_ADMITTED,
                digest=admission.request_digest,
            )
            return self._reception(trigger, decision, input_ids=input_ids)
        owners = self._live_own_tickets(trigger)
        if len(owners) == 1:
            owner = owners[0]
            if owner.status is TicketStatus.CANCELLED:
                decision = self._stop(trigger, now, "stopped_ignored", admission.request_digest)
                return self._reception(trigger, decision, ticket_id=owner.id)
            if owner.status in (TicketStatus.COMPLETED, TicketStatus.FAILED):
                stored = self._store_input(owner, trigger, signal, now)
                decision = self._observe(
                    trigger,
                    now,
                    ["resume_requires_operator"],
                    digest=admission.request_digest,
                )
                return self._reception(trigger, decision, ticket_id=owner.id, input_ids=[stored])
            decision = self._attach(trigger, now, "owned_thread_followup", admission.request_digest)
            return self._reception(trigger, decision, ticket_id=owner.id, input_ids=input_ids)
        if len(owners) > 1:
            decision = self._observe(
                trigger, now, ["ambiguous_owner"], digest=admission.request_digest
            )
            return self._reception(trigger, decision, input_ids=input_ids)
        existing = self._existing_ticket(admission)
        if existing is not None:
            if primary is not None:
                ensure_subscription(
                    self._store,
                    ids=self._ids,
                    ticket_id=existing.id,
                    thread_key=primary.key,
                    mode=SubscriptionMode.CONTEXT,
                    created_at=now,
                )
            decision = self._attach(trigger, now, "duplicate_request", admission.request_digest)
            return self._reception(trigger, decision, ticket_id=existing.id, input_ids=input_ids)
        if primary is None:
            decision = self._ignore(trigger, now, "thread_unknown", DecisionStrength.NOT_ADMITTED)
            return self._reception(trigger, decision, input_ids=input_ids)
        ticket = self._accept(signal, admission, primary, now)
        decision = record_decision(
            self._store,
            ids=self._ids,
            created_at=now,
            trigger_ids=[trigger.id],
            outcome=DecisionOutcome.ACCEPT,
            strength=DecisionStrength.EXPLICIT,
            reason_codes=["admitted"],
            policy_version=self._policy.policy_version,
            request_digest=admission.request_digest,
            mandate_id=ticket.mandate_id,
        )
        return self._reception(trigger, decision, ticket_id=ticket.id, input_ids=input_ids)

    def _route_inferred(
        self,
        trigger: Trigger,
        threads: list[Thread],
        now: int,
        input_ids: list[str],
        *,
        reevaluate: bool = False,
    ) -> Reception:
        if self._config.mode is AutonomyMode.OFF:
            decision = self._ignore(trigger, now, "autonomy_off", DecisionStrength.INFERRED)
            return self._reception(trigger, decision, input_ids=input_ids)
        context = self._assessment_context(trigger, threads)
        try:
            assessment = self._assessor.assess(context)
        except Exception:
            decision = record_decision(
                self._store,
                ids=self._ids,
                created_at=now,
                trigger_ids=[trigger.id],
                outcome=DecisionOutcome.UNAVAILABLE,
                strength=DecisionStrength.INFERRED,
                reason_codes=["assessor_error"],
                policy_version=self._policy.policy_version,
                request_digest=trigger.digest,
            )
            return self._reception(trigger, decision, input_ids=input_ids)
        reasons = list(assessment.reason_codes)
        if reevaluate:
            reasons.append("reevaluated")
        if self._config.mode is AutonomyMode.AUTO and assessment.would_accept:
            accepted = self._try_auto_accept(trigger, threads, reasons, now, input_ids)
            if accepted is not None:
                return accepted
        decision = self._observe(trigger, now, reasons, digest=trigger.digest)
        return self._reception(trigger, decision, input_ids=input_ids)

    def _try_auto_accept(
        self,
        trigger: Trigger,
        threads: list[Thread],
        reasons: list[str],
        now: int,
        input_ids: list[str],
    ) -> Reception | None:
        primary = threads[0] if threads else None
        if primary is None:
            return None
        if self._auto_gate is None:
            reasons.append("auto_gate_missing")
            return None
        gate = self._auto_gate.evaluate(
            tenant_id=trigger.tenant_id,
            repository=self._config.repository,
            reason_codes=reasons,
            now_ms=now,
        )
        if not gate.allowed:
            reasons.extend(gate.reason_codes)
            return None
        task = f"auto:{trigger.kind.value}:{trigger.external_id}"
        signal = RequestSignal(
            text=task,
            mentioned=False,
            stable_id=trigger.external_id,
            url=None,
        )
        admission = Admission(
            admitted=True,
            reason_code="auto_rule",
            requester=trigger.actor,
            repository=self._config.repository,
            stable_id=trigger.external_id,
            request_digest=policy_request_digest(task),
        )
        ticket = self._accept(signal, admission, primary, now)
        if gate.rule_id is not None:
            self._auto_gate.record_accept(gate.rule_id, now_ms=now)
        accept_reasons = [*reasons, *gate.reason_codes, "auto_accepted"]
        decision = record_decision(
            self._store,
            ids=self._ids,
            created_at=now,
            trigger_ids=[trigger.id],
            outcome=DecisionOutcome.ACCEPT,
            strength=DecisionStrength.INFERRED,
            reason_codes=accept_reasons,
            policy_version=self._policy.policy_version,
            request_digest=admission.request_digest,
            mandate_id=ticket.mandate_id,
        )
        return self._reception(trigger, decision, ticket_id=ticket.id, input_ids=input_ids)

    def _accept(
        self,
        signal: RequestSignal,
        admission: Admission,
        primary: Thread,
        now: int,
    ) -> Ticket:
        mandate = self._policy.create_mandate(
            admission,
            actions=self._config.actions,
            allowed_paths=self._config.allowed_paths,
            allowed_commands=self._config.allowed_commands,
            allowed_mcp=self._config.allowed_mcp,
            network_profile=self._config.network_profile,
            base_ref=self._config.base_ref,
            requires_approval=self._config.requires_approval,
        )
        ticket = Ticket(
            id=self._ids.new_id("tk"),
            title=signal.text[:40],
            task=signal.text,
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
                payload=signal.text,
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
        ensure_subscription(
            self._store,
            ids=self._ids,
            ticket_id=ticket.id,
            thread_key=primary.key,
            mode=SubscriptionMode.OWN,
            created_at=now,
        )
        return ticket

    # ------------------------------------------------------------------ 判定辅助
    def _read_signal(self, trigger: Trigger) -> tuple[RequestSignal | None, str | None]:
        if trigger.kind is TriggerKind.CI_STATUS:
            return None, None
        try:
            return RequestSignal.from_trigger(trigger), None
        except RouterPayloadError:
            return None, "malformed_request"

    def _admit(self, trigger: Trigger, signal: RequestSignal | None) -> Admission | None:
        if signal is None:
            return None
        admission = self._policy.admit_explicit(
            ExplicitRequest(
                repository=self._config.repository,
                requester=trigger.actor,
                mentioned=signal.mentioned,
                stable_id=signal.stable_id,
                text=signal.text,
            )
        )
        if self._audit is not None:
            self._audit.admitted(admission)
        return admission

    def _already_decided(self, trigger_id: str) -> bool:
        return bool(self._store.list_decisions(trigger_id=trigger_id, limit=1))

    def _live_subscriptions(self, trigger: Trigger, mode: SubscriptionMode) -> list[Subscription]:
        seen: set[str] = set()
        result: list[Subscription] = []
        for thread_key in trigger.thread_keys:
            for subscription in self._store.list_subscriptions(
                thread_key=thread_key, live_only=True
            ):
                if subscription.mode is not mode or subscription.id in seen:
                    continue
                seen.add(subscription.id)
                result.append(subscription)
        return result

    def _live_own_tickets(self, trigger: Trigger) -> list[Ticket]:
        tickets: dict[str, Ticket] = {}
        for subscription in self._live_subscriptions(trigger, SubscriptionMode.OWN):
            ticket = self._store.get_ticket(subscription.ticket_id)
            if ticket is not None:
                tickets[ticket.id] = ticket
        return list(tickets.values())

    def _assessment_context(self, trigger: Trigger, threads: list[Thread]) -> AssessmentContext:
        owners = self._live_own_tickets(trigger)
        return AssessmentContext(
            trigger=trigger,
            thread=threads[0] if threads else None,
            has_live_own_subscription=bool(owners),
            has_live_context_subscription=bool(
                self._live_subscriptions(trigger, SubscriptionMode.CONTEXT)
            ),
            existing_ticket_id=owners[0].id if len(owners) == 1 else None,
            policy_version=self._policy.policy_version,
        )

    def _existing_ticket(self, admission: Admission) -> Ticket | None:
        decision = self._store.find_accepted_decision(admission.request_digest)
        if decision is None or decision.mandate_id is None:
            return None
        mandate = self._store.get_mandate(decision.mandate_id)
        if mandate is None:
            return None
        return self._store.get_ticket_by_mandate_scope(mandate.scope, origin_kind="github")

    def _fanout(self, trigger: Trigger, now: int, signal: RequestSignal | None) -> list[str]:
        seen: set[str] = set()
        input_ids: list[str] = []
        for thread_key in trigger.thread_keys:
            for subscription in self._store.list_subscriptions(
                thread_key=thread_key, live_only=True
            ):
                ticket = self._store.get_ticket(subscription.ticket_id)
                if ticket is None or ticket.status in TERMINAL_TICKET_STATUSES:
                    continue
                if ticket.id in seen:
                    continue
                seen.add(ticket.id)
                input_ids.append(self._append_input(ticket, trigger, signal, now))
        return input_ids

    def _append_input(
        self,
        ticket: Ticket,
        trigger: Trigger,
        signal: RequestSignal | None,
        now: int,
    ) -> str:
        sequence = len(self._store.list_inputs(ticket.id)) + 1
        item = TicketInput(
            id=self._ids.new_id("in"),
            ticket_id=ticket.id,
            sequence=sequence,
            kind="text",
            payload=_input_text(trigger, signal),
            created_at=now,
        )
        self._store.insert_input(item)
        if ticket.status is not TicketStatus.RUNNING:
            self._store.update_ticket(
                ticket.model_copy(update={"woken_at": ticket.woken_at or now})
            )
        self._store.enqueue_effect(
            Effect(
                id=self._ids.new_id("ef"),
                kind=EffectKind.DELIVER_INPUT,
                idempotency_key=f"deliver:{ticket.id}:{sequence}",
                payload={
                    "ticket_id": ticket.id,
                    "input_id": item.id,
                    "sequence": sequence,
                },
                due_at=now,
            )
        )
        return item.id

    def _store_input(
        self, ticket: Ticket, trigger: Trigger, signal: RequestSignal, now: int
    ) -> str:
        text = _input_text(trigger, signal)
        for item in self._store.list_inputs(ticket.id):
            if item.state == "pending" and item.payload == text:
                return item.id
        sequence = len(self._store.list_inputs(ticket.id)) + 1
        item = TicketInput(
            id=self._ids.new_id("in"),
            ticket_id=ticket.id,
            sequence=sequence,
            kind="text",
            payload=text,
            created_at=now,
        )
        self._store.insert_input(item)
        return item.id

    def _reception(
        self,
        trigger: Trigger,
        decision: Decision,
        *,
        ticket_id: str | None = None,
        input_ids: list[str] | None = None,
    ) -> Reception:
        ids = list(input_ids or [])
        return Reception(
            trigger_id=trigger.id,
            decision_ids=[decision.id],
            ticket_id=ticket_id,
            input_ids=ids,
            fanned_out=len(ids),
        )

    def _ignore(
        self,
        trigger: Trigger,
        now: int,
        reason: str,
        strength: DecisionStrength,
        *,
        digest: str | None = None,
    ) -> Decision:
        return record_decision(
            self._store,
            ids=self._ids,
            created_at=now,
            trigger_ids=[trigger.id],
            outcome=DecisionOutcome.IGNORE,
            strength=strength,
            reason_codes=[reason],
            policy_version=self._policy.policy_version,
            request_digest=digest or trigger.digest,
        )

    def _observe(
        self,
        trigger: Trigger,
        now: int,
        reasons: list[str],
        *,
        digest: str | None = None,
    ) -> Decision:
        return record_decision(
            self._store,
            ids=self._ids,
            created_at=now,
            trigger_ids=[trigger.id],
            outcome=DecisionOutcome.OBSERVE,
            strength=DecisionStrength.INFERRED,
            reason_codes=list(reasons),
            policy_version=self._policy.policy_version,
            request_digest=digest or trigger.digest,
        )

    def _attach(self, trigger: Trigger, now: int, reason: str, digest: str) -> Decision:
        return record_decision(
            self._store,
            ids=self._ids,
            created_at=now,
            trigger_ids=[trigger.id],
            outcome=DecisionOutcome.ATTACH,
            strength=DecisionStrength.EXPLICIT,
            reason_codes=[reason],
            policy_version=self._policy.policy_version,
            request_digest=digest,
        )

    def _stop(self, trigger: Trigger, now: int, reason: str, digest: str) -> Decision:
        return record_decision(
            self._store,
            ids=self._ids,
            created_at=now,
            trigger_ids=[trigger.id],
            outcome=DecisionOutcome.STOP,
            strength=DecisionStrength.EXPLICIT,
            reason_codes=[reason],
            policy_version=self._policy.policy_version,
            request_digest=digest,
        )
