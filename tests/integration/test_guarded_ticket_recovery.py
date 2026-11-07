"""F5：崩溃后重放，GitHub 的 PR / 评论 / push 各只发生一次（步骤 3.11）。

装置是"真库 + 真临时 git 仓库 + fake 客户端"：崩溃点用「effect 已被 claim 成 running、
handler 还没跑」表示——这是进程在发 HTTP 之前死掉的那段窗口。claim 之后、请求发出之后的
崩溃无法在客户端侧识别（GitHub 的这三个写接口没有幂等键），不在本用例的保证范围内。
"""

from __future__ import annotations

import pathlib
import subprocess

from stableagent.control.approval import ApprovalGate, ApprovalService
from stableagent.control.audit import AuditWriter
from stableagent.control.model import Action
from stableagent.control.policy import StaticPolicy, StaticPolicyConfig
from stableagent.integrations.errors import GitHubApiError
from stableagent.integrations.github_client import FakeGitHubClient
from stableagent.integrations.github_effects import (
    github_comment_handler,
    github_effect_key,
    github_pull_request_handler,
)
from stableagent.orchestration.model import Effect, EffectKind, EffectState, ReportKind
from stableagent.orchestration.outbox import EffectDispatcher, EffectHandler
from stableagent.orchestration.store import Store
from tests.support import FixedClock, SequenceIds
from tests.unit.test_github_integration import _approve, _enqueue, _payload, _seed


def _git(cwd: pathlib.Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return result.stdout


def _make_repo(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """裸远端 + 工作副本，副本上有一条未推送的 `sa/tk-1` 提交。"""
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", str(remote))
    clone = tmp_path / "clone"
    _git(tmp_path, "init", "-b", "main", str(clone))
    identity = ("-c", "user.name=Test", "-c", "user.email=test@example.com")
    (clone / "README.md").write_text("base\n", encoding="utf-8")
    _git(clone, "add", "README.md")
    _git(clone, *identity, "commit", "-m", "init")
    _git(clone, "remote", "add", "origin", str(remote))
    _git(clone, "push", "origin", "main")
    _git(clone, "checkout", "-b", "sa/tk-1")
    (clone / "fix.txt").write_text("fix\n", encoding="utf-8")
    _git(clone, "add", "fix.txt")
    _git(clone, *identity, "commit", "-m", "fix")
    return clone, remote


def _handlers(
    store: Store,
    policy: StaticPolicy,
    audit: AuditWriter,
    gate: ApprovalGate,
    *,
    client: FakeGitHubClient,
    clock: FixedClock,
    ids: SequenceIds,
    push=None,
) -> dict[EffectKind, EffectHandler]:
    return {
        EffectKind.GITHUB_COMMENT: github_comment_handler(
            store=store, policy=policy, audit=audit, gate=gate, client=client, clock=clock, ids=ids
        ),
        EffectKind.GITHUB_PULL_REQUEST: github_pull_request_handler(
            store=store,
            policy=policy,
            audit=audit,
            gate=gate,
            client=client,
            clock=clock,
            ids=ids,
            push_branch=push,
        ),
    }


def _env(
    db: pathlib.Path,
    *,
    client: FakeGitHubClient,
    clock: FixedClock,
    ids: SequenceIds,
    push=None,
):
    store = Store(db, clock=clock)
    policy = StaticPolicy(
        StaticPolicyConfig(
            repository="owner/repo",
            actors=frozenset({"octocat"}),
            grantable_actions=frozenset(Action) - {Action.ENABLE_AUTO_MERGE},
            default_validity_ms=60_000,
        ),
        clock=clock,
        ids=ids,
    )
    audit = AuditWriter(policy, store, clock=clock, ids=ids)
    approvals = ApprovalService(store, clock=clock, ids=ids)
    gate = ApprovalGate(policy, approvals, audit)
    handlers = _handlers(store, policy, audit, gate, client=client, clock=clock, ids=ids, push=push)
    return store, policy, approvals, handlers


async def test_replay_after_crash_writes_pr_comment_and_push_once(
    tmp_path: pathlib.Path,
) -> None:
    clock = FixedClock()
    ids = SequenceIds()
    db = tmp_path / "guarded.db"
    client = FakeGitHubClient()
    clone, remote = _make_repo(tmp_path)
    pushed: list[str] = []

    async def push(head: str) -> None:
        _git(clone, "push", "origin", f"refs/heads/{head}:refs/heads/{head}")
        pushed.append(head)

    store, policy, approvals, handlers = _env(db, client=client, clock=clock, ids=ids, push=push)
    try:
        ticket, assignment, _ = _seed(store, policy)
        comment = _payload(ticket, assignment, EffectKind.GITHUB_COMMENT)
        pr = _payload(ticket, assignment, EffectKind.GITHUB_PULL_REQUEST)
        _enqueue(
            store,
            EffectKind.GITHUB_COMMENT,
            github_effect_key(EffectKind.GITHUB_COMMENT, str(comment["stable_id"]), "c"),
            comment,
            effect_id="ef-c",
        )
        pr_effect = _enqueue(
            store,
            EffectKind.GITHUB_PULL_REQUEST,
            github_effect_key(EffectKind.GITHUB_PULL_REQUEST, str(pr["stable_id"]), "p"),
            pr,
            effect_id="ef-p",
        )
        _approve(approvals, Action.COMMENT_ISSUE, comment)
        _approve(approvals, Action.CREATE_PULL_REQUEST, pr)
        # 崩溃点：PR effect 已 claim 成 running，handler 还没跑，账没 settle。
        with store.transaction():
            store.update_effect(pr_effect.model_copy(update={"state": EffectState.RUNNING}))
    finally:
        store.close()

    replayed, _, _, replayed_handlers = _env(db, client=client, clock=clock, ids=ids, push=push)
    try:
        assert replayed.reclaim_running_effects(clock.now_ms()) == 1
        dispatcher = EffectDispatcher(replayed, replayed_handlers, clock=clock)
        assert await dispatcher.dispatch_due() == 2

        assert [name for name, _ in client.calls] == ["create_comment", "create_pull_request"]
        assert pushed == ["sa/tk-1"]
        branches = _git(tmp_path, "--git-dir", str(remote), "branch", "--list", "sa/tk-1")
        assert branches.strip().endswith("sa/tk-1")  # push 真到了远端

        settled_pr = replayed.get_effect("ef-p")
        settled_comment = replayed.get_effect("ef-c")
        assert settled_pr is not None and settled_pr.state is EffectState.SUCCEEDED
        assert settled_comment is not None and settled_comment.state is EffectState.SUCCEEDED
        assert settled_pr.attempts == 1  # reclaim 不额外记一次；重放只算一次真实执行
        assert (
            replayed._conn.execute(
                "SELECT COUNT(*) FROM effects"
                " WHERE kind IN ('github_comment','github_pull_request')"
            ).fetchone()[0]
            == 2
        )
        assert [r.kind for r in replayed.list_reports(ticket.id)] == []  # 成功写不产 Report
    finally:
        replayed.close()


async def test_duplicate_enqueue_does_not_revive_a_settled_effect(
    tmp_path: pathlib.Path,
) -> None:
    clock = FixedClock()
    ids = SequenceIds()
    client = FakeGitHubClient()
    store, policy, approvals, handlers = _env(
        tmp_path / "dup.db", client=client, clock=clock, ids=ids
    )
    try:
        ticket, assignment, _ = _seed(store, policy)
        payload = _payload(ticket, assignment, EffectKind.GITHUB_COMMENT)
        key = github_effect_key(EffectKind.GITHUB_COMMENT, str(payload["stable_id"]), "c")
        _enqueue(store, EffectKind.GITHUB_COMMENT, key, payload, effect_id="ef-c")
        _approve(approvals, Action.COMMENT_ISSUE, payload)
        dispatcher = EffectDispatcher(store, handlers, clock=clock)
        assert await dispatcher.dispatch_due() == 1

        with store.transaction():
            again = store.enqueue_effect(
                _effect_of("ef-2", EffectKind.GITHUB_COMMENT, key, payload)
            )
        assert again.id == "ef-c" and again.state is EffectState.SUCCEEDED
        assert await dispatcher.dispatch_due() == 0
        assert len(client.calls) == 1
        assert store._conn.execute("SELECT COUNT(*) FROM effects").fetchone()[0] == 1
    finally:
        store.close()


async def test_retryable_failure_backs_off_then_succeeds_once(tmp_path: pathlib.Path) -> None:
    clock = FixedClock()
    ids = SequenceIds()
    client = FakeGitHubClient()
    store, policy, approvals, handlers = _env(
        tmp_path / "retry.db", client=client, clock=clock, ids=ids
    )
    try:
        ticket, assignment, _ = _seed(store, policy)
        payload = _payload(ticket, assignment, EffectKind.GITHUB_PULL_REQUEST)
        key = github_effect_key(EffectKind.GITHUB_PULL_REQUEST, str(payload["stable_id"]), "p")
        _enqueue(store, EffectKind.GITHUB_PULL_REQUEST, key, payload, effect_id="ef-p")
        _approve(approvals, Action.CREATE_PULL_REQUEST, payload)
        client.fail_next = GitHubApiError(500, "boom", "retryable")

        dispatcher = EffectDispatcher(store, handlers, clock=clock)
        assert await dispatcher.dispatch_due() == 0
        waiting = store.get_effect("ef-p")
        assert waiting is not None and waiting.state is EffectState.RETRY_WAIT
        assert waiting.attempts == 1
        assert [r.kind for r in store.list_reports(ticket.id)] == [ReportKind.FAILED]

        clock.advance(10_000)  # 越过 backoff 的 due_at
        assert await dispatcher.dispatch_due() == 1
        settled = store.get_effect("ef-p")
        assert settled is not None and settled.state is EffectState.SUCCEEDED
        assert settled.attempts == 2
        assert [name for name, _ in client.calls] == [
            "create_pull_request",
            "create_pull_request",
        ]
        assert (
            client.calls[0] == client.calls[1]
        )  # 重放同一请求；只有第二次成功（fail_next 已清空）
    finally:
        store.close()


def _effect_of(
    effect_id: str,
    kind: EffectKind,
    key: str,
    payload: dict[str, object],
) -> Effect:
    return Effect(id=effect_id, kind=kind, idempotency_key=key, payload=payload, due_at=0)
