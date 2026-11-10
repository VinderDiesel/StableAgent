"""DomainEventEmitter：脱敏 → 落库 → 指标；emit_safe 吞掉 sink 异常。"""

from __future__ import annotations

from typing import Any

from stableagent.observability import DEFAULT_TENANT
from stableagent.observability.events import (
    DomainEvent,
    DomainEventKind,
    canonical_attrs_digest,
)
from stableagent.observability.metrics import Metrics
from stableagent.observability.redaction import redact_mapping
from stableagent.observability.store import ObservabilityStore
from stableagent.observability.tracing import TraceRegistry
from stableagent.orchestration.model import Clock, IdFactory


class DomainEventEmitter:
    def __init__(
        self,
        store: ObservabilityStore,
        *,
        clock: Clock,
        ids: IdFactory,
        metrics: Metrics | None = None,
        traces: TraceRegistry | None = None,
        tenant_id: str = DEFAULT_TENANT,
    ) -> None:
        self._store = store
        self._clock = clock
        self._ids = ids
        self._metrics = metrics if metrics is not None else Metrics()
        self._traces = traces if traces is not None else TraceRegistry()
        self._tenant_id = tenant_id

    @property
    def metrics(self) -> Metrics:
        return self._metrics

    @property
    def traces(self) -> TraceRegistry:
        return self._traces

    def emit(
        self,
        kind: DomainEventKind,
        *,
        outcome: str,
        reason_code: str | None = None,
        ticket_id: str | None = None,
        assignment_id: str | None = None,
        run_id: str | None = None,
        effect_id: str | None = None,
        trigger_id: str | None = None,
        decision_id: str | None = None,
        attrs: dict[str, Any] | None = None,
    ) -> DomainEvent:
        safe_attrs = redact_mapping(dict(attrs or {}))
        event = DomainEvent(
            id=self._ids.new_id("ev"),
            timestamp=self._clock.now_ms(),
            tenant_id=self._tenant_id,
            kind=kind,
            outcome=outcome,
            reason_code=reason_code,
            ticket_id=ticket_id,
            assignment_id=assignment_id,
            run_id=run_id,
            effect_id=effect_id,
            trigger_id=trigger_id,
            decision_id=decision_id,
            payload_digest=canonical_attrs_digest(safe_attrs),
            attrs=safe_attrs,
        )
        # 独立短事务：调用方应在业务事务提交后再 emit_safe。
        with self._store.transaction():
            self._store.insert_domain_event(event)
        self._metrics.inc(
            "domain_events",
            labels={"kind": kind.value, "outcome": outcome},
        )
        if ticket_id:
            ctx = self._traces.link(ticket_id, event.id)
            for oid in (effect_id, trigger_id, decision_id, assignment_id, run_id):
                if oid:
                    ctx.link(oid)
        return event

    def emit_safe(
        self,
        kind: DomainEventKind,
        *,
        outcome: str,
        reason_code: str | None = None,
        ticket_id: str | None = None,
        assignment_id: str | None = None,
        run_id: str | None = None,
        effect_id: str | None = None,
        trigger_id: str | None = None,
        decision_id: str | None = None,
        attrs: dict[str, Any] | None = None,
    ) -> DomainEvent | None:
        try:
            return self.emit(
                kind,
                outcome=outcome,
                reason_code=reason_code,
                ticket_id=ticket_id,
                assignment_id=assignment_id,
                run_id=run_id,
                effect_id=effect_id,
                trigger_id=trigger_id,
                decision_id=decision_id,
                attrs=attrs,
            )
        except Exception:
            self._metrics.inc("emit_failures")
            return None
