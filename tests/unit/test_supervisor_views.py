"""只读监管视图：优先级顺序、失败 effect 归属、时间线不泄露 payload。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stableagent.orchestration.model import (
    Assignment,
    AssignmentStatus,
    Effect,
    EffectKind,
    EffectState,
    Mandate,
    Report,
    ReportKind,
    Ticket,
    TicketInput,
    TicketStatus,
)
from stableagent.supervisor.model import (
    Decision,
    DecisionOutcome,
    DecisionStrength,
    SourceCursor,
    SourceHealth,
    Trigger,
    TriggerKind,
)
from stableagent.supervisor.store import SupervisorStore
from stableagent.supervisor.views import SupervisorViews
from tests.support import FixedClock


@pytest.fixture
def store(tmp_path: Path) -> SupervisorStore:
    return SupervisorStore(tmp_path / "p6.db", clock=FixedClock())


def _ticket(store: SupervisorStore, ticket_id: str, status: TicketStatus) -> Ticket:
    clock = FixedClock()
    store.insert_mandate(Mandate(id=f"md-{ticket_id}", granted_at=clock.now_ms()))
    ticket = Ticket(
        id=ticket_id,
        title=f"title-{ticket_id}",
        task="SECRET-TASK",
        created_at=clock.now_ms(),
        status=status,
        mandate_id=f"md-{ticket_id}",
    )
    store.insert_ticket(ticket)
    return ticket


def test_needs_attention_is_ordered_by_actionability(store: SupervisorStore) -> None:
    waiting = _ticket(store, "tk-wait", TicketStatus.WAITING)
    failed = _ticket(store, "tk-fail", TicketStatus.RUNNING)
    ready = _ticket(store, "tk-ready", TicketStatus.READY)
    store.insert_approval(
        id="ap-1",
        ticket_id=waiting.id,
        action="push",
        resource_digest="d",
        reason="r",
        requested_at=1,
    )
    store.insert_assignment(
        Assignment(id="as-1", ticket_id=failed.id, status=AssignmentStatus.RUNNING)
    )
    store.enqueue_effect(
        Effect(
            id="ef-1",
            kind=EffectKind.GITHUB_COMMENT,
            idempotency_key="k-1",
            payload={"assignment_id": "as-1"},
            state=EffectState.FAILED,
            due_at=1,
        )
    )

    items = SupervisorViews(store).needs_attention()

    assert [(i.priority, i.source, i.ticket_id) for i in items] == [
        (0, "approval", waiting.id),
        (1, "failed_effect", failed.id),
        (2, "waiting_ticket", waiting.id),
        (3, "ready_ticket", ready.id),
    ]


def test_open_tickets_excludes_terminal_statuses(store: SupervisorStore) -> None:
    _ticket(store, "tk-run", TicketStatus.RUNNING)
    _ticket(store, "tk-done", TicketStatus.COMPLETED)
    _ticket(store, "tk-cancel", TicketStatus.CANCELLED)
    assert [t.id for t in SupervisorViews(store).open_tickets()] == ["tk-run"]


def test_failed_effects_carry_ticket_identity(store: SupervisorStore) -> None:
    failed = _ticket(store, "tk-fail", TicketStatus.RUNNING)
    store.insert_assignment(
        Assignment(id="as-1", ticket_id=failed.id, status=AssignmentStatus.RUNNING)
    )
    store.enqueue_effect(
        Effect(
            id="ef-1",
            kind=EffectKind.GITHUB_COMMENT,
            idempotency_key="k-1",
            payload={"assignment_id": "as-1"},
            state=EffectState.FAILED,
            due_at=1,
        )
    )
    rows = SupervisorViews(store).failed_effects()
    assert [(r.ticket_id, r.effect_id) for r in rows] == [(failed.id, "ef-1")]


def test_source_health_lists_persisted_cursors(store: SupervisorStore) -> None:
    store.put_source_cursor(SourceCursor(source="github", cursor="9", health=SourceHealth.DEGRADED))
    assert [c.health for c in SupervisorViews(store).source_health()] == [SourceHealth.DEGRADED]


def test_thread_timeline_includes_triggers_and_decisions_without_payload(
    store: SupervisorStore,
) -> None:
    trigger = Trigger(
        id="tg-1",
        external_id="issue:1",
        kind=TriggerKind.ISSUE,
        actor="alice",
        thread_keys=["issue:acme/widgets#1"],
        occurred_at=10,
        received_at=11,
        payload={"body": "DO-NOT-LEAK"},
    )
    store.insert_trigger(trigger)
    store.insert_decision(
        Decision(
            id="dc-1",
            trigger_ids=["tg-1"],
            outcome=DecisionOutcome.OBSERVE,
            strength=DecisionStrength.INFERRED,
            policy_version="p6-v1",
            request_digest="d-1",
            created_at=12,
        )
    )

    events = SupervisorViews(store).thread_timeline("issue:acme/widgets#1")

    assert [e.kind for e in events] == ["trigger", "decision"]
    assert "DO-NOT-LEAK" not in json.dumps([e.model_dump() for e in events])


def test_ticket_timeline_uses_sanctioned_summaries_only(store: SupervisorStore) -> None:
    ticket = _ticket(store, "tk-1", TicketStatus.RUNNING)
    store.insert_report(
        Report(
            id="rp-1",
            ticket_id=ticket.id,
            assignment_id="as-1",
            kind=ReportKind.PROGRESS,
            summary="local-restricted exit=0 output=intact duration_ms=0",
            created_at=20,
        )
    )
    store.insert_input(
        TicketInput(
            id="in-1",
            ticket_id=ticket.id,
            sequence=1,
            payload="RAW-SECRET-INPUT",
            created_at=21,
        )
    )

    events = SupervisorViews(store).ticket_timeline(ticket.id)

    assert [e.kind for e in events] == ["report", "input"]
    rendered = json.dumps([e.model_dump() for e in events])
    assert "exit=0" in rendered
    assert "RAW-SECRET-INPUT" not in rendered and "SECRET-TASK" not in rendered
