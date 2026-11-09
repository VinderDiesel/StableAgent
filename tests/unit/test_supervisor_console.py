"""WP-6.5：CLI 只做 operator 门禁、--confirm、退出码与渲染；业务写复用既有服务。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stableagent.control.approval import ApprovalService
from stableagent.control.model import Action, ApprovalStatus
from stableagent.orchestration.model import (
    Assignment,
    Effect,
    EffectKind,
    EffectState,
    Mandate,
    Report,
    ReportKind,
    Ticket,
    TicketStatus,
)
from stableagent.supervisor.cli import main
from stableagent.supervisor.console import (
    CliError,
    Console,
    ConsoleConfig,
    render_thread,
)
from stableagent.supervisor.model import (
    SourceCursor,
    Subscription,
    SubscriptionMode,
    Thread,
    ThreadKind,
    Trigger,
    TriggerKind,
)
from stableagent.supervisor.notifications import NotificationConfig, NotificationDrafter
from stableagent.supervisor.router import RouterPermissionError
from stableagent.supervisor.store import SupervisorStore
from tests.support import FixedClock, SequenceIds


def _config(**overrides):
    data = {
        "repository": "o/r",
        "operators": frozenset({"alice"}),
        "actors": frozenset({"alice"}),
        "actions": ("read_repository", "comment_issue"),
        "default_validity_ms": 60_000,
        "notifications": NotificationConfig(enabled=True, max_chars=400),
    }
    data.update(overrides)
    return ConsoleConfig(**data)


def _seed_ticket(
    store: SupervisorStore,
    *,
    ticket_id: str = "tk-1",
    status: TicketStatus = TicketStatus.RUNNING,
) -> Ticket:
    suffix = ticket_id.removeprefix("tk-")
    mandate_id = f"md-{suffix}"
    store.insert_mandate(
        Mandate(
            id=mandate_id,
            origin_kind="github",
            scope="github:o/r:issue:1",
            granted_at=1,
            repository="o/r",
            actions=["comment_issue"],
        )
    )
    ticket = Ticket(
        id=ticket_id,
        title="Fix parser",
        task="SECRET-TASK",
        created_at=1,
        status=status,
        mandate_id=mandate_id,
    )
    store.insert_ticket(ticket)
    store.insert_assignment(Assignment(id=f"as-{suffix}", ticket_id=ticket_id, attempt=1))
    store.upsert_thread(
        Thread(
            key="issue:o/r#1",
            repository="o/r",
            kind=ThreadKind.ISSUE,
            external_ref="#1",
            last_trigger_at=1,
        )
    )
    store.insert_subscription(
        Subscription(
            id=f"sb-{suffix}",
            ticket_id=ticket_id,
            thread_key="issue:o/r#1",
            mode=SubscriptionMode.OWN,
            created_at=1,
        )
    )
    return ticket


@pytest.fixture
def env(tmp_path: Path):
    clock = FixedClock()
    ids = SequenceIds()
    store = SupervisorStore(tmp_path / "p6.db", clock=clock)
    approvals = ApprovalService(store, clock=clock, ids=ids)
    return store, approvals, clock, ids


def _console(store, clock, ids, **overrides) -> Console:
    return Console(store, clock=clock, ids=ids, config=_config(**overrides))


def _draft(store, approvals, clock) -> None:
    drafter = NotificationDrafter(
        store,
        approvals,
        NotificationConfig(enabled=True, max_chars=400),
        repository="o/r",
        clock=clock,
    )
    assert drafter.draft_due() == 1


def test_non_operator_is_rejected_before_confirm(env) -> None:
    store, approvals, clock, ids = env
    _seed_ticket(store)
    _draft(store, approvals, clock)
    approval = approvals.for_ticket("tk-1", status=ApprovalStatus.PENDING)[0]
    console = _console(store, clock, ids)

    with pytest.raises(RouterPermissionError):
        console.approve(approval.id, actor="mallory", confirm="wrong")

    assert approvals.get(approval.id).status is ApprovalStatus.PENDING


def test_confirm_mismatch_writes_nothing(env) -> None:
    store, approvals, clock, ids = env
    _seed_ticket(store)
    _draft(store, approvals, clock)
    approval = approvals.for_ticket("tk-1", status=ApprovalStatus.PENDING)[0]
    console = _console(store, clock, ids)

    with pytest.raises(CliError) as captured:
        console.approve(approval.id, actor="alice", confirm="ap-other")

    assert captured.value.code == 2
    assert approvals.get(approval.id).status is ApprovalStatus.PENDING


def test_approve_notification_enqueues_matching_effect(env) -> None:
    store, approvals, clock, ids = env
    _seed_ticket(store)
    _draft(store, approvals, clock)
    approval = approvals.for_ticket("tk-1", status=ApprovalStatus.PENDING)[0]
    console = _console(store, clock, ids)

    decided = console.approve(approval.id, actor="alice", confirm=approval.id)

    assert decided.status is ApprovalStatus.APPROVED
    effects = [
        effect
        for effect in store.due_effects(clock.now_ms())
        if effect.kind is EffectKind.GITHUB_COMMENT
    ]
    assert [effect.idempotency_key for effect in effects] == ["notify:tk-1:accepted"]
    assert effects[0].payload["body"].startswith("[Accepted] Fix parser")


def test_approve_rejects_a_changed_draft_without_writing(env) -> None:
    store, approvals, clock, ids = env
    ticket = _seed_ticket(store)
    _draft(store, approvals, clock)
    approval = approvals.for_ticket("tk-1", status=ApprovalStatus.PENDING)[0]
    store.update_ticket(ticket.model_copy(update={"title": "changed"}))
    console = _console(store, clock, ids)

    with pytest.raises(CliError) as captured:
        console.approve(approval.id, actor="alice", confirm=approval.id)

    assert captured.value.code == 3
    assert approvals.get(approval.id).status is ApprovalStatus.PENDING
    assert store.due_effects(clock.now_ms()) == []


def test_generic_approval_flips_without_draft_recompute(env) -> None:
    store, approvals, clock, ids = env
    _seed_ticket(store)
    approval = approvals.submit("tk-1", Action.COMMENT_ISSUE, "legacy", "high_risk")
    console = _console(store, clock, ids)

    decided = console.approve(approval.id, actor="alice", confirm=approval.id)

    assert decided.status is ApprovalStatus.APPROVED
    assert store.due_effects(clock.now_ms()) == []


def test_deny_silences_future_notifications(env) -> None:
    store, approvals, clock, ids = env
    _seed_ticket(store)
    _draft(store, approvals, clock)
    approval = approvals.for_ticket("tk-1", status=ApprovalStatus.PENDING)[0]
    console = _console(store, clock, ids)

    console.deny(approval.id, actor="alice", confirm=approval.id)

    drafter = NotificationDrafter(
        store, approvals, NotificationConfig(), repository="o/r", clock=clock
    )
    assert drafter.draft_due() == 0


def test_stop_revokes_pending_approvals_and_cancels(env) -> None:
    store, approvals, clock, ids = env
    _seed_ticket(store)
    _draft(store, approvals, clock)
    console = _console(store, clock, ids)

    stopped = console.stop("tk-1", actor="alice", confirm="tk-1")

    assert stopped.status is TicketStatus.CANCELLED
    assert approvals.for_ticket("tk-1", status=ApprovalStatus.PENDING) == []


def test_retry_dispatches_failed_effect_then_failed_ticket(env) -> None:
    store, approvals, clock, ids = env
    _seed_ticket(store)
    store.enqueue_effect(
        Effect(
            id="ef-1",
            kind=EffectKind.GITHUB_COMMENT,
            idempotency_key="notify:tk-1:accepted",
            payload={"ticket_id": "tk-1"},
            state=EffectState.FAILED,
            attempts=3,
            due_at=1,
            last_error="boom",
        )
    )
    _seed_ticket(store, ticket_id="tk-2", status=TicketStatus.FAILED)
    console = _console(store, clock, ids)

    effect = console.retry("ef-1", actor="alice", confirm="ef-1")
    assignment = console.retry("tk-2", actor="alice", confirm="tk-2")

    assert effect.state is EffectState.PENDING
    assert assignment.attempt == 2
    assert store.get_ticket("tk-2").status is TicketStatus.QUEUED
    with pytest.raises(CliError) as captured:
        console.retry("missing", actor="alice", confirm="missing")
    assert captured.value.code == 1


def test_pause_and_resume_require_literal_confirm(env) -> None:
    store, _, clock, ids = env
    console = _console(store, clock, ids)

    assert console.pause(actor="alice", confirm="pause").paused is True
    assert console.resume(actor="alice", confirm="resume").paused is False
    with pytest.raises(CliError) as captured:
        console.resume(actor="alice", confirm="pause")
    assert captured.value.code == 2


def test_cli_main_status_and_exit_codes(tmp_path: Path, capsys) -> None:
    db = tmp_path / "p6.db"
    config_path = tmp_path / "console.json"
    config_path.write_text(
        json.dumps(
            {
                "repository": "o/r",
                "operators": ["alice"],
                "actors": ["alice"],
                "actions": ["read_repository", "comment_issue"],
                "default_validity_ms": 60000,
                "notifications": {"enabled": True, "max_chars": 400, "since_ms": 0},
            }
        ),
        encoding="utf-8",
    )
    store = SupervisorStore(db, clock=FixedClock())
    _seed_ticket(store, status=TicketStatus.READY)
    store.close()
    base = ["--db", str(db), "--config", str(config_path), "--operator", "alice"]

    assert main([*base, "status"]) == 0
    output = capsys.readouterr().out
    assert "attention" in output and "ready_ticket" in output
    assert main([*base, "stop", "tk-1"]) == 2
    assert main([*base[:5], "mallory", "stop", "tk-1", "--confirm", "tk-1"]) == 4
    assert main([*base, "ticket", "tk-missing"]) == 1


def test_cli_main_covers_ticket_thread_retry_and_controls(tmp_path: Path, capsys) -> None:
    db = tmp_path / "p6.db"
    config_path = tmp_path / "console.json"
    config_path.write_text(
        json.dumps(
            {
                "repository": "o/r",
                "operators": ["alice"],
                "actors": ["alice"],
                "actions": ["read_repository", "comment_issue"],
                "default_validity_ms": 60000,
                "notifications": {"enabled": True, "max_chars": 400, "since_ms": 0},
            }
        ),
        encoding="utf-8",
    )
    store = SupervisorStore(db, clock=FixedClock())
    _seed_ticket(store, status=TicketStatus.READY)
    store.insert_report(
        Report(
            id="rp-1",
            ticket_id="tk-1",
            assignment_id="as-1",
            kind=ReportKind.READY,
            summary="ready for review",
            created_at=2,
        )
    )
    store.enqueue_effect(
        Effect(
            id="ef-1",
            kind=EffectKind.GITHUB_COMMENT,
            idempotency_key="k-1",
            payload={"ticket_id": "tk-1"},
            state=EffectState.FAILED,
            due_at=1,
        )
    )
    store.insert_trigger(
        Trigger(
            id="tg-1",
            external_id="issue:1",
            kind=TriggerKind.ISSUE,
            actor="alice",
            thread_keys=["issue:o/r#1"],
            occurred_at=1,
            received_at=1,
            payload={"request": {"text": "task", "mentioned": True}},
        )
    )
    store.insert_approval(
        id="ap-1",
        ticket_id="tk-1",
        action="comment_issue",
        resource_digest="legacy",
        reason="high_risk",
        requested_at=1,
    )
    store.insert_approval(
        id="ap-2",
        ticket_id="tk-1",
        action="comment_issue",
        resource_digest="legacy-2",
        reason="high_risk",
        requested_at=1,
    )
    store.put_source_cursor(SourceCursor(source="github", cursor="9"))
    store.close()
    base = ["--db", str(db), "--config", str(config_path), "--operator", "alice"]

    assert main([*base, "ticket", "tk-1"]) == 0
    ticket_output = capsys.readouterr().out
    assert "assignment as-1" in ticket_output
    assert "timeline 2 report rp-1" in ticket_output
    assert main([*base, "thread", "issue:o/r#1"]) == 0
    assert "trigger" in capsys.readouterr().out
    assert main([*base, "approve", "ap-1", "--confirm", "ap-1"]) == 0
    assert main([*base, "deny", "ap-2", "--confirm", "ap-2"]) == 0
    assert main([*base, "approve", "ap-missing", "--confirm", "ap-missing"]) == 1
    assert render_thread([]) == "thread empty"
    assert main([*base, "retry", "ef-1", "--confirm", "ef-1"]) == 0
    assert main([*base, "reevaluate", "tg-1", "--confirm", "tg-1"]) == 0
    assert main([*base, "pause", "--confirm", "pause"]) == 0
    assert main([*base, "resume", "--confirm", "resume"]) == 0

    missing = tmp_path / "missing.json"
    assert main(["--db", str(db), "--config", str(missing), "--operator", "alice", "status"]) == 1
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"repository": "o/r", "bogus": 1}), encoding="utf-8")
    assert main(["--db", str(db), "--config", str(bad), "--operator", "alice", "status"]) == 1
    bad_action = tmp_path / "bad-action.json"
    bad_action.write_text(
        json.dumps({"repository": "o/r", "operators": ["alice"], "actions": ["merge"]}),
        encoding="utf-8",
    )
    assert (
        main(
            [
                "--db",
                str(db),
                "--config",
                str(bad_action),
                "--operator",
                "alice",
                "status",
            ]
        )
        == 1
    )
