"""步骤 2.6 的接线半边：命令经 P4 effect 执行，`CommandResult` 的结果写进 Assignment Report。

C7 已钉住命令边界本身（allowlist / cwd / 超时 / 输出上限 / 脱敏）；这里只用真 `Store`、
真 `CommandRunner`、真 `subprocess` 与 `EffectDispatcher` 证明"结果 → Report"这一段没有
第二套文本规则：Report 的 `summary` 就是 `summary()` 那一行，`details` 只放结构化字段，
stdout/stderr 一个字符都不进（它们可能带密钥，而 Report 是长期留存的那一份）。
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

from stableagent.execution.command import CommandRunner
from stableagent.execution.effects import command_idempotency_key, run_command_handler
from stableagent.execution.naming import workspace_name
from stableagent.execution.sandbox import LocalRestrictedRunner
from stableagent.orchestration.model import (
    Assignment,
    AssignmentStatus,
    Effect,
    EffectKind,
    EffectState,
    Mandate,
    ReportKind,
    Ticket,
    TicketStatus,
)
from stableagent.orchestration.outbox import EffectDispatcher
from stableagent.orchestration.store import Store
from tests.support import FixedClock, SequenceIds

TICKET_ID = "tk-0123456789abcdef"


def _rig(
    tmp_path: pathlib.Path,
    *,
    allowlist: tuple[str, ...] = (sys.executable,),
    redact: tuple[str, ...] = (),
    max_output_bytes: int = 64 * 1024,
):
    clock = FixedClock()
    ids = SequenceIds()
    store = Store(tmp_path / "p5.db", clock=clock)
    store.insert_mandate(
        Mandate(
            id="md-1",
            granted_at=clock.now_ms(),
            repository="acme/widgets",
            base_ref="main",
        )
    )
    ticket = Ticket(
        id=TICKET_ID,
        title="add rate limiter",
        task="t",
        created_at=clock.now_ms(),
        status=TicketStatus.RUNNING,
        mandate_id="md-1",
    )
    store.insert_ticket(ticket)
    assignment = Assignment(id="as-1", ticket_id=TICKET_ID, status=AssignmentStatus.RUNNING)
    store.insert_assignment(assignment)
    worktree = tmp_path / "worktrees" / workspace_name(ticket)
    worktree.mkdir(parents=True)
    runner = LocalRestrictedRunner(
        CommandRunner(
            frozenset(allowlist),
            worktree_root=tmp_path / "worktrees",
            clock=clock,
            max_output_bytes=max_output_bytes,
            redact=redact,
        )
    )
    handler = run_command_handler(runner, store, clock=clock, ids=ids)
    dispatcher = EffectDispatcher(store, {EffectKind.RUN_COMMAND: handler}, clock=clock)
    return store, ticket, assignment, worktree, dispatcher, clock, ids


def _effect(
    ticket: Ticket,
    assignment: Assignment,
    argv: tuple[str, ...],
    *,
    cwd: str,
    clock: FixedClock,
    ids: SequenceIds,
    sequence: int = 1,
    timeout_s: float | None = None,
) -> Effect:
    payload: dict[str, object] = {
        "ticket_id": ticket.id,
        "assignment_id": assignment.id,
        "argv": list(argv),
        "cwd": cwd,
    }
    if timeout_s is not None:
        payload["timeout_s"] = timeout_s
    return Effect(
        id=ids.new_id("ef"),
        kind=EffectKind.RUN_COMMAND,
        idempotency_key=command_idempotency_key(assignment.id, sequence),
        payload=payload,
        due_at=clock.now_ms(),
    )


async def _dispatch(store: Store, effect: Effect, dispatcher: EffectDispatcher) -> Effect:
    with store.transaction():
        store.enqueue_effect(effect)
    await dispatcher.dispatch_due()
    settled = store.get_effect(effect.id)
    assert settled is not None
    return settled


async def test_allowed_command_lands_its_summary_in_the_assignment_report(
    tmp_path: pathlib.Path,
) -> None:
    """验收口径：Report 的 summary 与 details 就是 `CommandResult` 的事实，正文不跟着走。"""
    store, ticket, assignment, _, dispatcher, clock, ids = _rig(tmp_path)
    effect = _effect(
        ticket,
        assignment,
        (sys.executable, "-c", "print('hello from the command')"),
        cwd=workspace_name(ticket),
        clock=clock,
        ids=ids,
    )

    settled = await _dispatch(store, effect, dispatcher)

    assert settled.state is EffectState.SUCCEEDED
    reports = store.list_reports(ticket.id)
    assert len(reports) == 1
    report = reports[0]
    assert (report.assignment_id, report.kind) == (assignment.id, ReportKind.PROGRESS)
    assert report.summary == "local-restricted exit=0 output=intact duration_ms=0"
    assert report.details == {
        "mode": "local-restricted",
        "exit_code": 0,
        "denied_reason": None,
        "timed_out": False,
        "truncated": False,
        "duration_ms": 0,
    }
    assert "hello from the command" not in json.dumps(report.model_dump(), default=str)


async def test_denied_command_writes_a_blocked_report_without_spawning(
    tmp_path: pathlib.Path,
) -> None:
    """拒绝同样是一条结果：Report 写稳定码，命令没有 spawn，worktree 里没有副作用。"""
    store, ticket, assignment, worktree, dispatcher, clock, ids = _rig(
        tmp_path, allowlist=("python3",)
    )
    effect = _effect(
        ticket,
        assignment,
        (sys.executable, "-c", "open('pwned.txt', 'w').write('x')"),
        cwd=workspace_name(ticket),
        clock=clock,
        ids=ids,
    )

    settled = await _dispatch(store, effect, dispatcher)

    assert settled.state is EffectState.SUCCEEDED  # 拒绝已记录，effect 本身没有被判死
    report = store.list_reports(ticket.id)[0]
    assert report.kind is ReportKind.BLOCKED
    assert report.summary == (
        "local-restricted denied=command_not_allowed output=intact duration_ms=0"
    )
    assert report.details["denied_reason"] == "command_not_allowed"
    assert not (worktree / "pwned.txt").exists()


async def test_cwd_escape_writes_a_blocked_report(tmp_path: pathlib.Path) -> None:
    """payload 里的 cwd 也过同一道收口：越界在 Report 里是 `cwd_escape`，不是异常堆栈。"""
    store, ticket, assignment, _, dispatcher, clock, ids = _rig(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    effect = _effect(
        ticket,
        assignment,
        (sys.executable, "-c", "print('never runs')"),
        cwd=str(outside),
        clock=clock,
        ids=ids,
    )

    await _dispatch(store, effect, dispatcher)

    report = store.list_reports(ticket.id)[0]
    assert report.kind is ReportKind.BLOCKED
    assert report.summary == "local-restricted denied=cwd_escape output=intact duration_ms=0"
    assert report.details["denied_reason"] == "cwd_escape"


async def test_truncation_and_redaction_reach_the_report_without_stream_text(
    tmp_path: pathlib.Path,
) -> None:
    """先脱敏后截断的顺序由 runner 守着；Report 只留 `output=truncated` 与布尔字段。"""
    store, ticket, assignment, _, dispatcher, clock, ids = _rig(
        tmp_path, redact=("SEKRET",), max_output_bytes=128
    )
    effect = _effect(
        ticket,
        assignment,
        (sys.executable, "-c", "print('A' * 200 + 'SEKRET' + 'A' * 200)"),
        cwd=workspace_name(ticket),
        clock=clock,
        ids=ids,
    )

    await _dispatch(store, effect, dispatcher)

    report = store.list_reports(ticket.id)[0]
    assert report.summary == "local-restricted exit=0 output=truncated duration_ms=0"
    assert report.details["truncated"] is True
    rendered = json.dumps(report.model_dump(), default=str)
    assert "SEKRET" not in rendered
    assert "AAAA" not in rendered
    assert "stdout" not in report.details and "stderr" not in report.details


@pytest.mark.parametrize(
    ("payload", "detail"),
    [
        ({}, "payload_missing:ticket_id"),
        (
            {"ticket_id": "tk-missing", "assignment_id": "as-1", "argv": [], "cwd": "."},
            "ticket_missing:tk-missing",
        ),
        ({"ticket_id": TICKET_ID, "argv": [], "cwd": "."}, "payload_missing:assignment_id"),
        (
            {"ticket_id": TICKET_ID, "assignment_id": "as-1", "argv": "git", "cwd": "."},
            "payload_invalid:argv",
        ),
        (
            {"ticket_id": TICKET_ID, "assignment_id": "as-1", "argv": ["git", 1], "cwd": "."},
            "payload_invalid:argv",
        ),
        (
            {"ticket_id": TICKET_ID, "assignment_id": "as-1", "argv": ["git"]},
            "payload_missing:cwd",
        ),
        (
            {
                "ticket_id": TICKET_ID,
                "assignment_id": "as-1",
                "argv": ["git"],
                "cwd": ".",
                "timeout_s": "60",
            },
            "payload_invalid:timeout_s",
        ),
    ],
)
async def test_malformed_payload_fails_permanently_without_running(
    tmp_path: pathlib.Path, payload: dict[str, object], detail: str
) -> None:
    """坏 payload 是永久失败（重放只会同样拒），不 spawn、不写 Report、不进退避预算。"""
    store, ticket, _, _, dispatcher, clock, ids = _rig(tmp_path)
    effect = Effect(
        id=ids.new_id("ef"),
        kind=EffectKind.RUN_COMMAND,
        idempotency_key=command_idempotency_key("as-1", 99),
        payload=payload,
        due_at=clock.now_ms(),
    )
    with store.transaction():
        store.enqueue_effect(effect)

    await dispatcher.dispatch_due()

    settled = store.get_effect(effect.id)
    assert settled is not None and settled.state is EffectState.FAILED
    assert detail in (settled.last_error or "")
    assert store.list_reports(ticket.id) == []


async def test_command_without_a_complete_mandate_fails_permanently(
    tmp_path: pathlib.Path,
) -> None:
    """子进程环境里的仓库身份只能来自 Mandate：缺哪一项就永久拒，不拿空串凑数。"""
    store, _, _, _, dispatcher, clock, ids = _rig(tmp_path)
    variants = [
        (
            "tk-aaaaaaaaaaaaaaaa",
            None,
            "mandate_missing:md-absent",
        ),
        (
            "tk-bbbbbbbbbbbbbbbb",
            Mandate(id="md-norepo", granted_at=clock.now_ms(), base_ref="main"),
            "mandate_missing:repository",
        ),
        (
            "tk-cccccccccccccccc",
            Mandate(
                id="md-nobase",
                granted_at=clock.now_ms(),
                repository="acme/widgets",
            ),
            "mandate_missing:base_ref",
        ),
    ]
    for ticket_id, mandate, detail in variants:
        if mandate is not None:
            store.insert_mandate(mandate)
        store.insert_ticket(
            Ticket(
                id=ticket_id,
                title="t",
                task="t",
                created_at=clock.now_ms(),
                mandate_id=mandate.id if mandate is not None else "md-absent",
            )
        )
        effect = Effect(
            id=ids.new_id("ef"),
            kind=EffectKind.RUN_COMMAND,
            idempotency_key=command_idempotency_key(ticket_id, 1),
            payload={
                "ticket_id": ticket_id,
                "assignment_id": "as-1",
                "argv": ["git"],
                "cwd": ".",
            },
            due_at=clock.now_ms(),
        )
        with store.transaction():
            store.enqueue_effect(effect)

        await dispatcher.dispatch_due()

        settled = store.get_effect(effect.id)
        assert settled is not None and settled.state is EffectState.FAILED
        assert detail in (settled.last_error or ""), ticket_id
        assert store.list_reports(ticket_id) == []
