"""P5 演示：显式 GitHub 请求在授权、worktree、命令与审批四道门之间走完一次真实写入。

- 装置全是本地的：裸 git 远端 + 工作克隆 + `FakeGitHubClient`，不读 token、不连公网。
- `--phase run`：请求 → 受理五件原子写 → `worktree` 复检 → 路径授权写文件 →
  allowlist 命令提交 → 人工批准 → 同一个 PR effect 里 push 与建 PR。
- `--phase reject`：五类拒绝各出一条结构化记录，外部调用计数必须为 0。
- 事件按行输出 JSON；末行 `RUN_RESULT ...` 或 `REJECT_RESULT ...`。

用法：
    python examples/05_guarded_github_task.py --phase run --dir .demo-guarded
    python examples/05_guarded_github_task.py --phase reject --dir .demo-guarded
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from stableagent.control.approval import ApprovalGate, ApprovalService
from stableagent.control.audit import AuditWriter
from stableagent.control.errors import AdmissionRejectedError
from stableagent.control.model import Action, Resource, resource_digest
from stableagent.control.policy import StaticPolicy, StaticPolicyConfig
from stableagent.execution.command import CommandContext, CommandRequest, CommandRunner
from stableagent.execution.effects import command_idempotency_key, run_command_handler
from stableagent.execution.git import SubprocessGit
from stableagent.execution.sandbox import LocalRestrictedRunner
from stableagent.execution.worktree import GitWorktreeManager
from stableagent.integrations.github import ExplicitGitHubRequest, GitHubIntake
from stableagent.integrations.github_client import FakeGitHubClient
from stableagent.integrations.github_effects import (
    github_effect_key,
    github_pull_request_handler,
    github_resource,
)
from stableagent.integrations.repository import guarded_prepare_worktree_handler
from stableagent.orchestration.model import (
    Clock,
    Effect,
    EffectKind,
    EffectState,
    IdFactory,
    ReportKind,
)
from stableagent.orchestration.outbox import EffectDispatcher
from stableagent.orchestration.store import Store

REPOSITORY = "owner/repo"
STABLE_ID = "github:owner/repo:issue_comment:1"
ACTOR = "octocat"
COMMIT_SUBJECT = "fix off-by-one"


class DemoClock:
    """每次 now_ms() 前进 1ms：单进程内确定，不读墙钟。"""

    def __init__(self, start: int = 1_700_000_000_000) -> None:
        self._now = start

    def now_ms(self) -> int:
        self._now += 1
        return self._now


class DemoIds:
    """Ticket 用 `tk-<8 位十六进制>`：`workspace_name` 要求这个形状，其余前缀按序号。"""

    def __init__(self) -> None:
        self._seq: dict[str, int] = {}

    def new_id(self, prefix: str) -> str:
        self._seq[prefix] = self._seq.get(prefix, 0) + 1
        if prefix == "tk":
            return f"tk-{self._seq[prefix]:08x}"
        return f"{prefix}-{self._seq[prefix]:04d}"


def _emit(event: str, **fields: Any) -> None:
    print(json.dumps({"event": event, **fields}, ensure_ascii=False, sort_keys=True))


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return done.stdout


def _make_fixture(root: Path) -> tuple[Path, Path]:
    """裸远端 + 工作克隆；克隆里有一条含 off-by-one 的 main 提交。"""
    root.mkdir(parents=True, exist_ok=True)
    remote = root / "remote.git"
    clone = root / "clone"
    _git(root, "init", "--bare", str(remote))
    clone.mkdir()
    _git(clone, "init", "-b", "main")
    (clone / "README.md").write_text("demo\n", encoding="utf-8")
    (clone / "src").mkdir()
    (clone / "src" / "app.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    identity = ("-c", "user.name=Demo", "-c", "user.email=demo@example.invalid")
    _git(clone, "add", ".")
    _git(clone, *identity, "commit", "-m", "init")
    _git(clone, "remote", "add", "origin", str(remote))
    _git(clone, "push", "origin", "main")
    return clone, remote


def _policy(clock: Clock, ids: IdFactory, *, commands: list[str] | None = None) -> StaticPolicy:
    return StaticPolicy(
        StaticPolicyConfig(
            repository=REPOSITORY,
            actors=frozenset({ACTOR}),
            grantable_actions=frozenset(Action) - {Action.ENABLE_AUTO_MERGE},
            allowed_commands=list(commands or ["git"]),
            default_validity_ms=3_600_000,
        ),
        clock=clock,
        ids=ids,
    )


def _request(**changes: Any) -> ExplicitGitHubRequest:
    base: dict[str, Any] = {
        "repository": REPOSITORY,
        "url": "https://github.com/owner/repo/issues/7#issuecomment-1",
        "actor": ACTOR,
        "text": "@agent fix the off-by-one in src/app.py",
        "mentioned": True,
        "received_at": 1_700_000_000_000,
        "stable_id": STABLE_ID,
    }
    return ExplicitGitHubRequest(**{**base, **changes})


def _authorize(
    policy: StaticPolicy,
    audit: AuditWriter,
    mandate: Any,
    action: Action,
    resource: Resource,
    *,
    ticket_id: str,
    assignment_id: str,
) -> None:
    decision = policy.authorize(mandate, action, resource)
    audit.authorized(
        mandate,
        action,
        resource,
        decision,
        ticket_id=ticket_id,
        assignment_id=assignment_id,
    )
    if not decision.allowed:
        raise SystemExit(f"authorization denied: {action.value}/{decision.reason}")


async def _run_phase(root: Path) -> int:
    clock = DemoClock()
    ids = DemoIds()
    clone, _remote = _make_fixture(root)
    store = Store(root / "guarded.db", clock=clock)
    try:
        policy = _policy(clock, ids)
        audit = AuditWriter(policy, store, clock=clock, ids=ids)
        intake = GitHubIntake(store, policy, audit, clock=clock, ids=ids)
        outcome = intake.submit(
            _request(),
            actions=[
                Action.PREPARE_WORKTREE,
                Action.READ_REPOSITORY,
                Action.WRITE_REPOSITORY,
                Action.RUN_COMMAND,
                Action.COMMIT_CHANGES,
                Action.PUSH_BRANCH,
                Action.CREATE_PULL_REQUEST,
            ],
            allowed_paths=["src/"],
            allowed_commands=["git"],
            base_ref="main",
        )
        if not outcome.admission.admitted:
            raise SystemExit(f"admission denied: {outcome.admission.reason_code}")
        assert outcome.ticket_id is not None
        ticket = store.get_ticket(outcome.ticket_id)
        if ticket is None:
            raise SystemExit("ticket missing after admission")
        assignments = store.list_assignments(ticket.id)
        if not assignments:
            raise SystemExit("assignment missing after admission")
        assignment = assignments[0]
        mandate = store.get_mandate(ticket.mandate_id)
        if mandate is None:
            raise SystemExit("mandate missing after admission")
        _emit(
            "request",
            ticket=ticket.id,
            assignment=assignment.id,
            admitted=outcome.admission.admitted,
            duplicate=outcome.duplicate,
        )

        manager = GitWorktreeManager(root / "worktrees", git=SubprocessGit(), clock=clock)
        approvals = ApprovalService(store, clock=clock, ids=ids)
        gate = ApprovalGate(policy, approvals, audit)
        client = FakeGitHubClient(
            results={"create_pull_request": {"html_url": "https://example.invalid/pull/1"}}
        )
        pushed: list[str] = []
        handle_holder: dict[str, Any] = {}

        async def push(head: str) -> None:
            handle = handle_holder["handle"]
            _git(handle.path, "push", "origin", f"HEAD:refs/heads/{head}")
            pushed.append(head)
            _emit("push", head=head)

        runner = LocalRestrictedRunner(
            CommandRunner(frozenset({"git"}), worktree_root=root / "worktrees", clock=clock)
        )
        handlers = {
            EffectKind.PREPARE_WORKTREE: guarded_prepare_worktree_handler(
                manager,
                store,
                policy=policy,
                audit=audit,
                clock=clock,
                ids=ids,
            ),
            EffectKind.RUN_COMMAND: run_command_handler(runner, store, clock=clock, ids=ids),
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
        dispatcher = EffectDispatcher(store, handlers, clock=clock)

        with store.transaction():
            store.enqueue_effect(
                Effect(
                    id=ids.new_id("ef"),
                    kind=EffectKind.PREPARE_WORKTREE,
                    idempotency_key=f"prepare:{assignment.id}",
                    payload={
                        "ticket_id": ticket.id,
                        "assignment_id": assignment.id,
                        "repository": str(clone),
                        "base_ref": "main",
                    },
                    due_at=clock.now_ms(),
                )
            )
        await dispatcher.dispatch_due()
        # 复用同一现场：第二次 prepare 不新建目录与分支。
        handle = manager.prepare(ticket, repository=str(clone), base_ref="main")
        handle_holder["handle"] = handle
        _emit("worktree", ticket=ticket.id, branch=handle.branch, path=str(handle.path))

        write_resource = Resource(kind="path", path="src/app.py")
        _authorize(
            policy,
            audit,
            mandate,
            Action.WRITE_REPOSITORY,
            write_resource,
            ticket_id=ticket.id,
            assignment_id=assignment.id,
        )
        target = handle.path / "src" / "app.py"
        target.write_text(
            target.read_text(encoding="utf-8").replace("return a - b", "return a + b"),
            encoding="utf-8",
        )
        _emit("write", path="src/app.py", action=Action.WRITE_REPOSITORY.value)

        command_sequence = 0

        async def run_command(*argv: str) -> None:
            nonlocal command_sequence
            _authorize(
                policy,
                audit,
                mandate,
                Action.RUN_COMMAND,
                Resource(kind="command", command=argv[0]),
                ticket_id=ticket.id,
                assignment_id=assignment.id,
            )
            command_sequence += 1
            effect = Effect(
                id=ids.new_id("ef"),
                kind=EffectKind.RUN_COMMAND,
                idempotency_key=command_idempotency_key(assignment.id, command_sequence),
                payload={
                    "ticket_id": ticket.id,
                    "assignment_id": assignment.id,
                    "argv": list(argv),
                    "cwd": str(handle.path),
                },
                due_at=clock.now_ms(),
            )
            with store.transaction():
                store.enqueue_effect(effect)
            await dispatcher.dispatch_due()
            settled = store.get_effect(effect.id)
            report = store.list_reports(ticket.id)[-1]
            if (
                settled is None
                or settled.state is not EffectState.SUCCEEDED
                or report.details["denied_reason"] is not None
                or report.details["exit_code"] != 0
            ):
                raise SystemExit(f"command failed: {argv!r} {report.summary}")
            _emit("command", argv=list(argv), exit=report.details["exit_code"], report=report.id)

        await run_command("git", "status", "--short")
        await run_command("git", "add", "src/app.py")
        await run_command(
            "git",
            "-c",
            "user.name=Demo",
            "-c",
            "user.email=demo@example.invalid",
            "commit",
            "-m",
            COMMIT_SUBJECT,
        )

        payload: dict[str, Any] = {
            "ticket_id": ticket.id,
            "assignment_id": assignment.id,
            "stable_id": STABLE_ID,
            "repository": REPOSITORY,
            "head": handle.branch,
            "base": "main",
            "title": "Fix off-by-one",
            "body": "Demo change produced in the guarded worktree.",
        }
        pr_resource = github_resource(Action.CREATE_PULL_REQUEST, payload)
        digest = resource_digest(Action.CREATE_PULL_REQUEST, pr_resource)
        approval = approvals.submit(
            ticket.id,
            Action.CREATE_PULL_REQUEST,
            digest,
            "demo_high_risk",
            assignment_id=assignment.id,
        )
        _emit("approval", approval=approval.id, status=approval.status.value)
        approvals.decide(approval.id, True, ACTOR)
        _emit("approval", approval=approval.id, status="approved", by=ACTOR)

        pr_effect = Effect(
            id=ids.new_id("ef"),
            kind=EffectKind.GITHUB_PULL_REQUEST,
            idempotency_key=github_effect_key(EffectKind.GITHUB_PULL_REQUEST, STABLE_ID, digest),
            payload=payload,
            due_at=clock.now_ms(),
        )
        with store.transaction():
            store.enqueue_effect(pr_effect)
        await dispatcher.dispatch_due()
        settled = store.get_effect(pr_effect.id)
        _emit(
            "pr",
            calls=len(client.calls),
            head=handle.branch,
            effect=pr_effect.id,
            state=None if settled is None else settled.state.value,
        )
        print(
            f"RUN_RESULT status=completed ticket={ticket.id} branch={handle.branch} "
            f"pr_calls={len(client.calls)} push_calls={len(pushed)}"
        )
        return 0 if len(client.calls) == 1 and pushed == [handle.branch] else 1
    finally:
        store.close()


def _fresh(
    root: Path,
) -> tuple[Store, DemoClock, DemoIds, StaticPolicy, AuditWriter, GitHubIntake]:
    """每个拒绝场景一套独立装置：库、目录、id 序列互不污染。"""
    root.mkdir(parents=True, exist_ok=True)
    clock = DemoClock()
    ids = DemoIds()
    store = Store(root / "reject.db", clock=clock)
    policy = _policy(clock, ids)
    audit = AuditWriter(policy, store, clock=clock, ids=ids)
    intake = GitHubIntake(store, policy, audit, clock=clock, ids=ids)
    return store, clock, ids, policy, audit, intake


async def _reject_actor(root: Path) -> tuple[str, int, int, int]:
    store, _clock, _ids, _policy_, audit, intake = _fresh(root)
    try:
        outcome = intake.submit(
            _request(actor="mallory"),
            actions=[Action.RUN_COMMAND],
            allowed_commands=["git"],
        )
        assert outcome.admission.reason_code == "actor_not_allowlisted", outcome
        assert store.list_tickets() == []
        return "actor_not_allowlisted", len(store.list_audit()), 0, 0
    finally:
        store.close()


async def _reject_mention(root: Path) -> tuple[str, int, int, int]:
    store, _clock, _ids, _policy_, audit, intake = _fresh(root)
    try:
        outcome = intake.submit(
            _request(mentioned=False),
            actions=[Action.RUN_COMMAND],
            allowed_commands=["git"],
        )
        assert outcome.admission.reason_code == "mention_required", outcome
        assert store.list_tickets() == []
        return "mention_required", len(store.list_audit()), 0, 0
    finally:
        store.close()


async def _reject_command(root: Path) -> tuple[str, int, int, int]:
    store, clock, _ids, policy, audit, intake = _fresh(root)
    try:
        outcome = intake.submit(
            _request(),
            actions=[Action.RUN_COMMAND],
            allowed_commands=["git"],
        )
        assert outcome.ticket_id is not None
        ticket = store.get_ticket(outcome.ticket_id)
        assert ticket is not None and ticket.mandate_id is not None
        mandate = store.get_mandate(ticket.mandate_id)
        assert mandate is not None
        resource = Resource(kind="command", command="rm")
        decision = policy.authorize(mandate, Action.RUN_COMMAND, resource)
        audit.authorized(mandate, Action.RUN_COMMAND, resource, decision, ticket_id=ticket.id)
        assert decision.reason == "command_not_allowed", decision

        runner = CommandRunner(frozenset({"git"}), worktree_root=root / "worktrees", clock=clock)
        result = runner.run(
            CommandRequest(
                argv=("rm", "-rf", "/"),
                cwd=root / "worktrees",
                context=CommandContext(
                    ticket_id=ticket.id,
                    assignment_id=store.list_assignments(ticket.id)[0].id,
                    repository=REPOSITORY,
                    base_ref="main",
                ),
            )
        )
        assert result.denied_reason == "command_not_allowed", result
        assert result.started_at == result.ended_at, result
        return "command_not_allowed", len(store.list_audit()), 0, 0
    finally:
        store.close()


async def _reject_approval(root: Path) -> tuple[str, int, int, int]:
    store, clock, ids, policy, audit, intake = _fresh(root)
    try:
        outcome = intake.submit(
            _request(),
            actions=[Action.CREATE_PULL_REQUEST],
            base_ref="main",
        )
        assert outcome.ticket_id is not None
        ticket = store.get_ticket(outcome.ticket_id)
        assert ticket is not None
        assignment = store.list_assignments(ticket.id)[0]
        gate = ApprovalGate(policy, ApprovalService(store, clock=clock, ids=ids), audit)
        client = FakeGitHubClient()
        pushes: list[str] = []

        async def push(head: str) -> None:
            pushes.append(head)

        dispatcher = EffectDispatcher(
            store,
            {
                EffectKind.GITHUB_PULL_REQUEST: github_pull_request_handler(
                    store=store,
                    policy=policy,
                    audit=audit,
                    gate=gate,
                    client=client,
                    clock=clock,
                    ids=ids,
                    push_branch=push,
                )
            },
            clock=clock,
        )
        payload: dict[str, Any] = {
            "ticket_id": ticket.id,
            "assignment_id": assignment.id,
            "stable_id": STABLE_ID,
            "repository": REPOSITORY,
            "head": "sa-00000001-demo",
            "base": "main",
            "title": "Needs an approval first",
            "body": "No approval was submitted for this write.",
        }
        effect = Effect(
            id=ids.new_id("ef"),
            kind=EffectKind.GITHUB_PULL_REQUEST,
            idempotency_key="reject:approval_pending",
            payload=payload,
            due_at=clock.now_ms(),
        )
        with store.transaction():
            store.enqueue_effect(effect)
        await dispatcher.dispatch_due()
        settled = store.get_effect(effect.id)
        assert settled is not None and settled.state is EffectState.FAILED, settled
        assert client.calls == [] and pushes == []
        blocked = [
            report
            for report in store.list_reports(ticket.id)
            if report.kind is ReportKind.BLOCKED
            and report.details.get("reason") == "approval_pending"
        ]
        assert len(blocked) == 1, blocked
        return "approval_pending", len(store.list_audit()), 1, 0
    finally:
        store.close()


async def _reject_auto_merge(root: Path) -> tuple[str, int, int, int]:
    store, _clock, _ids, policy, audit, _intake = _fresh(root)
    try:
        try:
            StaticPolicyConfig(
                repository=REPOSITORY,
                actors=frozenset({ACTOR}),
                grantable_actions=frozenset({Action.ENABLE_AUTO_MERGE}),
                allowed_commands=["git"],
                default_validity_ms=3_600_000,
            )
        except AdmissionRejectedError as exc:
            assert "grantable_must_exclude_auto_merge" in str(exc), exc
        else:
            raise AssertionError("auto merge must not be grantable at construction")

        admission = policy.admit_explicit(_request().to_explicit_request())
        audit.admitted(admission)
        try:
            policy.create_mandate(admission, actions=[Action.ENABLE_AUTO_MERGE])
        except AdmissionRejectedError as exc:
            assert "auto_merge_not_grantable" in str(exc), exc
        else:
            raise AssertionError("auto merge must not be grantable when creating a mandate")
        assert store.list_tickets() == []
        return "auto_merge_not_grantable", len(store.list_audit()), 0, 0
    finally:
        store.close()


async def _reject_phase(root: Path) -> int:
    cases = (
        ("actor_not_allowlisted", _reject_actor),
        ("mention_required", _reject_mention),
        ("command_not_allowed", _reject_command),
        ("approval_pending", _reject_approval),
        ("auto_merge_not_grantable", _reject_auto_merge),
    )
    refused: list[str] = []
    results: list[tuple[str, int, int, int]] = []
    for expected, case in cases:
        try:
            result = await case(root / expected)
        except Exception as exc:  # 场景未按预期拒绝：结构化报错，绝不把异常当拒绝
            _emit("reject_error", reject=expected, error=repr(exc))
            return 1
        if result[0] != expected:
            _emit("reject_error", reject=expected, got=result[0])
            return 1
        _emit(
            "reject_case",
            reject=result[0],
            audit=result[1],
            effects=result[2],
            external_calls=result[3],
        )
        refused.append(result[0])
        results.append(result)
    ok = all(item[3] == 0 and item[1] >= 1 for item in results)
    print(f"REJECT_RESULT cases={len(refused)} ok={ok}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("run", "reject"), default="run")
    parser.add_argument("--dir", default=".demo-guarded")
    args = parser.parse_args(argv)
    root = Path(args.dir).resolve()
    if args.phase == "run":
        return asyncio.run(_run_phase(root))
    return asyncio.run(_reject_phase(root))


if __name__ == "__main__":
    sys.exit(main())
