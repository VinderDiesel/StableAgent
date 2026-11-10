"""P6 Beta 离线演练：多 Trigger、两个并发 Ticket、失败/等待人工/评论恢复各走一遍。

- 装置全是本地的：FakeGitHubReadClient + 确定性时钟 + 脚本化 Worker，不读 token、不连公网。
- `--phase run`：Issue/PR/CI Trigger 经 Source→Router 路由；两个 Ticket 并发执行，
  一个 FAILED、一个 WAITING、一个 READY→COMPLETED 后被后续评论经 operator resume 恢复；
  通知先草拟成审批，只批准一条，外部评论恰好一次。
- 事件按行输出 JSON；末行 `BETA_RESULT ...`。

用法：
    python examples/06_superagent_beta.py --phase run --dir .demo-beta
    python examples/06_superagent_beta.py --phase report --dir .demo-beta
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from stableagent.control.approval import ApprovalService
from stableagent.control.model import Action, ApprovalStatus
from stableagent.control.policy import StaticPolicy, StaticPolicyConfig
from stableagent.integrations.github import (
    FakeGitHubReadClient,
    GitHubReadItem,
    GitHubReadKind,
)
from stableagent.orchestration.model import (
    Assignment,
    AssignmentAction,
    Effect,
    EffectKind,
    ReportKind,
    Ticket,
    TicketAction,
    TicketInput,
    TicketStatus,
)
from stableagent.orchestration.service import OrchestrationService
from stableagent.supervisor.model import DecisionOutcome
from stableagent.supervisor.notifications import (
    NotificationConfig,
    NotificationDrafter,
    notification_effect,
)
from stableagent.supervisor.router import RouterConfig
from stableagent.supervisor.runtime import RuntimeBundle, RuntimeConfig, build_runtime
from stableagent.supervisor.source import GitHubSource
from stableagent.supervisor.store import SupervisorStore
from stableagent.supervisor.views import beta_report

REPOSITORY = "owner/repo"
BOT_LOGIN = "agent"
OPERATOR = "alice"
BASE = 1_700_000_000_000
TASK_A = "@agent fix the flaky parser test"
TASK_B = "@agent investigate the memory spike"
TASK_C = "@agent document the release checklist"
FOLLOW_UP = "@agent please also cover the empty-input case"
OUTCOMES = {TASK_A: "ready", TASK_B: "failed", TASK_C: "waiting"}


class DemoClock:
    """每次 now_ms() 前进 1ms：单进程内确定，不读墙钟。"""

    def __init__(self, start: int = BASE) -> None:
        self._now = start

    def now_ms(self) -> int:
        self._now += 1
        return self._now


class DemoIds:
    """确定性 ID：同前缀按序号递增。"""

    def __init__(self) -> None:
        self._seq: dict[str, int] = {}

    def new_id(self, prefix: str) -> str:
        self._seq[prefix] = self._seq.get(prefix, 0) + 1
        return f"{prefix}-{self._seq[prefix]:04d}"


def _emit(event: str, **fields: Any) -> None:
    print(json.dumps({"event": event, **fields}, ensure_ascii=False, sort_keys=True))


def _items() -> list[GitHubReadItem]:
    return [
        GitHubReadItem(
            kind=GitHubReadKind.ISSUE,
            occurred_at=BASE + 1,
            resource={
                "number": 1,
                "user": {"login": OPERATOR},
                "body": TASK_A,
                "html_url": "https://github.com/owner/repo/issues/1",
            },
        ),
        GitHubReadItem(
            kind=GitHubReadKind.ISSUE,
            occurred_at=BASE + 2,
            resource={
                "number": 2,
                "user": {"login": OPERATOR},
                "body": TASK_B,
                "html_url": "https://github.com/owner/repo/issues/2",
            },
        ),
        GitHubReadItem(
            kind=GitHubReadKind.ISSUE,
            occurred_at=BASE + 3,
            resource={
                "number": 4,
                "user": {"login": OPERATOR},
                "body": TASK_C,
                "html_url": "https://github.com/owner/repo/issues/4",
            },
        ),
        GitHubReadItem(
            kind=GitHubReadKind.PULL_REQUEST,
            occurred_at=BASE + 4,
            resource={
                "number": 3,
                "user": {"login": OPERATOR},
                "html_url": "https://github.com/owner/repo/pull/3",
            },
        ),
        GitHubReadItem(
            kind=GitHubReadKind.CI_STATUS,
            occurred_at=BASE + 5,
            resource={"id": 900, "actor": "github-actions", "sha": "abc123", "state": "success"},
        ),
        GitHubReadItem(
            kind=GitHubReadKind.ISSUE_COMMENT,
            occurred_at=BASE + 6,
            resource={
                "id": 501,
                "issue_number": 1,
                "user": {"login": BOT_LOGIN},
                "body": "I am working on it",
            },
        ),
        GitHubReadItem(
            kind=GitHubReadKind.ISSUE,
            occurred_at=BASE + 1,
            resource={"number": 1, "user": {"login": OPERATOR}, "body": TASK_A},
        ),
    ]


class ScriptedExecutor:
    """脚本化 Worker：launch 即按任务剧本落 Report 并翻态；不读外部正文做授权。"""

    def __init__(self, service: OrchestrationService) -> None:
        self._service = service
        self.launched: list[str] = []

    async def launch(
        self, assignment: Assignment, ticket: Ticket, inputs: Sequence[TicketInput]
    ) -> None:
        self.launched.append(assignment.id)
        outcome = OUTCOMES[ticket.task]
        if outcome == "failed":
            self._service.add_report(
                ticket_id=ticket.id,
                assignment_id=assignment.id,
                kind=ReportKind.FAILED,
                summary="Memory spike reproduced; needs a human decision",
            )
            self._service.finish_assignment(
                assignment.id, AssignmentAction.FAIL, failure="needs_human_decision"
            )
            self._service.transition_ticket(ticket.id, TicketAction.FAIL)
        elif outcome == "waiting":
            self._service.add_report(
                ticket_id=ticket.id,
                assignment_id=assignment.id,
                kind=ReportKind.WAITING_FOR_HUMAN,
                summary="Which release window should the checklist target?",
            )
            self._service.transition_ticket(ticket.id, TicketAction.WAIT)
        else:
            self._service.add_report(
                ticket_id=ticket.id,
                assignment_id=assignment.id,
                kind=ReportKind.READY,
                summary="Parser test deflaked; ready for review",
            )
            self._service.finish_assignment(assignment.id, AssignmentAction.END)
            self._service.transition_ticket(ticket.id, TicketAction.DELIVER)

    async def deliver(
        self, assignment: Assignment, ticket: Ticket, inputs: Sequence[TicketInput]
    ) -> None:
        return None

    async def cancel(self, assignment: Assignment) -> None:
        return None

    async def recover(self, assignment: Assignment, ticket: Ticket) -> None:
        return None


def _build(
    root: Path, client: FakeGitHubReadClient, comments: list[dict[str, Any]]
) -> tuple[
    SupervisorStore, DemoClock, DemoIds, RuntimeBundle, ScriptedExecutor, OrchestrationService
]:
    root.mkdir(parents=True, exist_ok=True)
    clock = DemoClock()
    ids = DemoIds()
    store = SupervisorStore(root / "beta.db", clock=clock)
    policy = StaticPolicy(
        StaticPolicyConfig(
            repository=REPOSITORY,
            actors=frozenset({OPERATOR}),
            grantable_actions=frozenset({Action.READ_REPOSITORY, Action.COMMENT_ISSUE}),
            default_validity_ms=3_600_000,
        ),
        clock=clock,
        ids=ids,
    )
    source = GitHubSource(store, client, repository=REPOSITORY, bot_login=BOT_LOGIN, clock=clock)
    orchestration = OrchestrationService(store, clock=clock, ids=ids)
    executor = ScriptedExecutor(orchestration)

    async def comment_handler(effect: Effect) -> None:
        comments.append(dict(effect.payload))

    bundle = build_runtime(
        store=store,
        policy=policy,
        source=source,
        executor=executor,
        effect_handlers={EffectKind.GITHUB_COMMENT: comment_handler},
        config=RuntimeConfig(
            router=RouterConfig(
                repository=REPOSITORY,
                operators=frozenset({OPERATOR}),
                actions=("read_repository", "comment_issue"),
            ),
            owner="beta-demo",
            lock_ttl_ms=60_000,
            stop_grace_ms=50,
            step_interval_ms=0,
            notifications=NotificationConfig(enabled=True, max_chars=400, since_ms=0),
        ),
        clock=clock,
        ids=ids,
        scheduler_max_concurrency=2,
    )
    return store, clock, ids, bundle, executor, orchestration


async def _drain(bundle: RuntimeBundle, *, rounds: int = 4) -> None:
    for _ in range(rounds):
        await bundle.runtime.step()
        await asyncio.sleep(0)


async def _run_phase(root: Path) -> int:
    client = FakeGitHubReadClient(_items())
    comments: list[dict[str, Any]] = []
    store, clock, ids, bundle, executor, orchestration = _build(root, client, comments)
    try:
        bundle.runtime.start()
        await _drain(bundle)
        tickets = {ticket.task: ticket for ticket in store.list_tickets()}
        assert set(tickets) == {TASK_A, TASK_B, TASK_C}, tickets
        ticket_a, ticket_b, ticket_c = tickets[TASK_A], tickets[TASK_B], tickets[TASK_C]
        assert ticket_a.status is TicketStatus.READY, ticket_a
        assert ticket_b.status is TicketStatus.FAILED, ticket_b
        assert ticket_c.status is TicketStatus.WAITING, ticket_c
        assert len(executor.launched) == 3, executor.launched
        _emit("routed", tickets=len(tickets), launched=len(executor.launched))

        approvals = ApprovalService(store, clock=clock, ids=ids)
        pending = approvals.for_ticket(ticket_b.id, status=ApprovalStatus.PENDING)
        failure = next(item for item in pending if item.reason == "notification:failure")
        drafter = NotificationDrafter(
            store,
            approvals,
            NotificationConfig(enabled=True, max_chars=400, since_ms=0),
            repository=REPOSITORY,
            clock=clock,
        )
        draft = drafter.build(ticket_b.id)
        assert draft is not None
        effect = notification_effect(draft, effect_id=ids.new_id("ef"), now=clock.now_ms())
        approvals.decide(failure.id, True, OPERATOR, effect=effect)
        await bundle.runtime.step()
        assert len(comments) == 1, comments
        _emit("approval", approval=failure.id, status="approved", external_comments=len(comments))

        orchestration.complete_ticket(ticket_a.id)
        client.items.append(
            GitHubReadItem(
                kind=GitHubReadKind.ISSUE_COMMENT,
                occurred_at=BASE + 10,
                resource={
                    "id": 502,
                    "issue_number": 1,
                    "user": {"login": OPERATOR},
                    "body": FOLLOW_UP,
                    "html_url": "https://github.com/owner/repo/issues/1#issuecomment-502",
                },
            )
        )
        await _drain(bundle)
        follow = store.get_trigger_by_external("github", "github:owner/repo:issue_comment:502")
        assert follow is not None
        decisions = store.list_decisions(trigger_id=follow.id)
        assert any(
            decision.outcome is DecisionOutcome.OBSERVE
            and "resume_requires_operator" in decision.reason_codes
            for decision in decisions
        ), decisions
        _emit("follow_up", trigger=follow.id, decision="resume_requires_operator")

        bundle.service.resume(ticket_a.id, trigger_id=follow.id, actor=OPERATOR)
        await _drain(bundle)
        resumed_ticket = store.get_ticket(ticket_a.id)
        assert resumed_ticket is not None and resumed_ticket.status is TicketStatus.READY
        attempts = [item.attempt for item in store.list_assignments(ticket_a.id)]
        assert attempts == [1, 2], attempts
        _emit("resume", ticket=ticket_a.id, attempts=attempts)

        await bundle.runtime.stop()
        all_tickets = store.list_tickets()
        resumed = sum(
            1
            for ticket in all_tickets
            if any(item.attempt > 1 for item in store.list_assignments(ticket.id))
        )
        duplicate_tickets = len(all_tickets) - len({ticket.mandate_id for ticket in all_tickets})
        bot_triggers = sum(1 for trigger in store.list_triggers() if trigger.actor == BOT_LOGIN)
        print(
            f"BETA_RESULT tickets={len(all_tickets)} resumed={resumed} "
            f"external_comments={len(comments)} duplicate_tickets={duplicate_tickets} "
            f"bot_triggers={bot_triggers}"
        )
        ok = (len(all_tickets), resumed, len(comments), duplicate_tickets, bot_triggers) == (
            3,
            1,
            1,
            0,
            0,
        )
        return 0 if ok else 1
    finally:
        store.close()


def _report_phase(root: Path) -> int:
    store = SupervisorStore(root / "beta.db", clock=DemoClock())
    try:
        report = beta_report(store)
        print(
            "BETA_REPORT "
            + json.dumps(report.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
        )
        return 0
    finally:
        store.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("run", "report"), default="run")
    parser.add_argument("--dir", default=".demo-beta")
    args = parser.parse_args(argv)
    root = Path(args.dir).resolve()
    if args.phase == "run":
        return asyncio.run(_run_phase(root))
    return _report_phase(root)


if __name__ == "__main__":
    sys.exit(main())
