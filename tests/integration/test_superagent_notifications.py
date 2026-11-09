"""WP-6.5 第 6 条：通知只在批准后写一次，拒绝静默，stop 撤销旧草稿。"""

from __future__ import annotations

from pathlib import Path

import pytest

from stableagent.control.approval import ApprovalService
from stableagent.control.errors import ApprovalDecidedError
from stableagent.control.model import Action, ApprovalStatus
from stableagent.control.policy import StaticPolicy, StaticPolicyConfig
from stableagent.integrations.github import (
    FakeGitHubReadClient,
    GitHubReadItem,
    GitHubReadKind,
)
from stableagent.orchestration.model import (
    Assignment,
    EffectKind,
    Mandate,
    Ticket,
    TicketStatus,
)
from stableagent.supervisor.model import (
    Subscription,
    SubscriptionMode,
    Thread,
    ThreadKind,
)
from stableagent.supervisor.notifications import (
    NotificationConfig,
    NotificationDrafter,
    notification_effect,
)
from stableagent.supervisor.router import RouterConfig
from stableagent.supervisor.runtime import RuntimeConfig, build_runtime
from stableagent.supervisor.source import GitHubSource
from stableagent.supervisor.store import SupervisorStore
from tests.support import FixedClock, SequenceIds


def _issue_item(number: int = 1) -> GitHubReadItem:
    return GitHubReadItem(
        kind=GitHubReadKind.ISSUE,
        occurred_at=1,
        resource={
            "number": number,
            "user": {"login": "alice"},
            "body": "@agent please add rate limiter",
        },
    )


class RecordingExecutor:
    def __init__(self) -> None:
        self.launched: list[str] = []

    async def launch(self, assignment, ticket, inputs) -> None:
        self.launched.append(assignment.id)

    async def deliver(self, assignment, ticket, inputs) -> None:
        return None

    async def cancel(self, assignment) -> None:
        return None

    async def recover(self, assignment, ticket) -> None:
        return None


def _build(
    *,
    store: SupervisorStore,
    client: FakeGitHubReadClient,
    handlers,
    clock: FixedClock,
    ids: SequenceIds,
    owner: str,
):
    policy = StaticPolicy(
        StaticPolicyConfig(
            repository="o/r",
            actors=frozenset({"alice"}),
            grantable_actions=frozenset({Action.READ_REPOSITORY, Action.COMMENT_ISSUE}),
            default_validity_ms=60_000,
        ),
        clock=clock,
        ids=ids,
    )
    source = GitHubSource(store, client, repository="o/r", bot_login="agent", clock=clock)
    return build_runtime(
        store=store,
        policy=policy,
        source=source,
        executor=RecordingExecutor(),
        effect_handlers=handlers,
        config=RuntimeConfig(
            router=RouterConfig(
                repository="o/r",
                operators=frozenset({"alice"}),
                actions=("read_repository", "comment_issue"),
            ),
            owner=owner,
            lock_ttl_ms=1_000,
            stop_grace_ms=10,
            step_interval_ms=0,
            notifications=NotificationConfig(enabled=True, max_chars=400, since_ms=0),
        ),
        clock=clock,
        ids=ids,
    )


def _seed_ticket(store: SupervisorStore, *, ticket_id: str, status: TicketStatus) -> Ticket:
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
    clock, ids = FixedClock(), SequenceIds()
    store = SupervisorStore(tmp_path / "p6.db", clock=clock)
    try:
        yield store, clock, ids
    finally:
        store.close()


async def test_draft_approve_dispatch_once_across_restart(env) -> None:
    store, clock, ids = env
    client = FakeGitHubReadClient([_issue_item()])
    calls: list[str] = []

    async def comment_handler(effect) -> None:
        calls.append(effect.id)

    handlers = {EffectKind.GITHUB_COMMENT: comment_handler}
    first = _build(
        store=store, client=client, handlers=handlers, clock=clock, ids=ids, owner="runtime-a"
    )
    first.runtime.start()
    assert (await first.runtime.step()).drafted == 1

    ticket = store.list_tickets()[0]
    approvals = ApprovalService(store, clock=clock, ids=ids)
    pending = approvals.for_ticket(ticket.id, status=ApprovalStatus.PENDING)
    assert [row.reason for row in pending] == ["notification:accepted"]
    drafter = NotificationDrafter(
        store,
        approvals,
        NotificationConfig(enabled=True, max_chars=400, since_ms=0),
        repository="o/r",
        clock=clock,
    )
    draft = drafter.build(ticket.id)
    assert draft is not None
    effect = notification_effect(draft, effect_id="ef-comment-1", now=clock.now_ms())
    assert effect.idempotency_key == f"notify:{ticket.id}:accepted"

    approvals.decide(pending[0].id, True, "alice", effect=effect)

    assert (await first.runtime.step()).external == 1
    assert calls == ["ef-comment-1"]

    await first.runtime.stop()
    restarted = _build(
        store=store, client=client, handlers=handlers, clock=clock, ids=ids, owner="runtime-b"
    )
    restarted.runtime.start()
    await restarted.runtime.step()

    assert calls == ["ef-comment-1"]


async def test_deny_silences_and_stop_revokes_old_draft(env) -> None:
    store, clock, ids = env
    _seed_ticket(store, ticket_id="tk-1", status=TicketStatus.RUNNING)
    _seed_ticket(store, ticket_id="tk-2", status=TicketStatus.RUNNING)
    approvals = ApprovalService(store, clock=clock, ids=ids)
    bundle = _build(
        store=store,
        client=FakeGitHubReadClient([]),
        handlers={},
        clock=clock,
        ids=ids,
        owner="runtime-a",
    )
    bundle.runtime.start()

    assert (await bundle.runtime.step()).drafted == 2

    denied = approvals.for_ticket("tk-1", status=ApprovalStatus.PENDING)[0]
    approvals.decide(denied.id, False, "alice")
    assert (await bundle.runtime.step()).drafted == 0
    assert approvals.for_ticket("tk-1", status=ApprovalStatus.PENDING) == []

    revoked = approvals.for_ticket("tk-2", status=ApprovalStatus.PENDING)[0]
    bundle.service.stop("tk-2", actor="alice")
    assert approvals.for_ticket("tk-2", status=ApprovalStatus.PENDING) == []
    with pytest.raises(ApprovalDecidedError):
        approvals.decide(revoked.id, True, "alice")

    await bundle.runtime.stop()


def test_each_status_drafts_at_most_once(env) -> None:
    store, clock, ids = env
    for ticket_id, status in (
        ("tk-1", TicketStatus.WAITING),
        ("tk-2", TicketStatus.READY),
        ("tk-3", TicketStatus.FAILED),
    ):
        _seed_ticket(store, ticket_id=ticket_id, status=status)
    approvals = ApprovalService(store, clock=clock, ids=ids)
    drafter = NotificationDrafter(
        store,
        approvals,
        NotificationConfig(enabled=True, max_chars=400, since_ms=0),
        repository="o/r",
        clock=clock,
    )

    assert drafter.draft_due() == 3
    assert drafter.draft_due() == 0
    reasons = {
        approval.reason
        for ticket_id in ("tk-1", "tk-2", "tk-3")
        for approval in approvals.for_ticket(ticket_id)
    }
    assert reasons == {
        "notification:attention",
        "notification:result",
        "notification:failure",
    }
