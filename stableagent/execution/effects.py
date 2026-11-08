"""worktree 创建与受限命令都由 outbox effect 驱动（规格 2.3 / 2.6）。

幂等有两层，缺一不可：账上——`UNIQUE(kind, idempotency_key)` 让同一个 `prepare:{assignment_id}`
（或 `command:{assignment_id}:{sequence}`）只有一行；现场——`GitWorktreeManager.prepare` 的复用
四要素让第二次执行既不新建目录也不新建分支（C2）。handler 是 `async def`（P4 的
`EffectHandler` 类型如此），`EffectDispatcher` 已断言 handler 不在事务内，L3 不受影响。
"""

from __future__ import annotations

from pathlib import Path

from stableagent.execution.command import CommandContext, CommandRequest
from stableagent.execution.sandbox import SandboxRunner
from stableagent.execution.worktree import GitWorktreeManager
from stableagent.orchestration.errors import PermanentEffectError
from stableagent.orchestration.model import (
    Clock,
    Effect,
    IdFactory,
    Report,
    ReportKind,
    SystemClock,
    Uuid4Id,
)
from stableagent.orchestration.outbox import EffectHandler
from stableagent.orchestration.store import Store

PREPARE_KEY_PREFIX = "prepare"
COMMAND_KEY_PREFIX = "command"


def prepare_idempotency_key(assignment_id: str) -> str:
    """键形状与 P4 的 `launch:{assignment.id}:{attempt}` 同源：外部稳定 id 打头。

    prepare 的键**不带 attempt**——一个 assignment 只有一个现场，重试同一次 assignment 必须复用
    同一目录与同一分支（规格 2.3），所以重试不能换键。
    """
    return f"{PREPARE_KEY_PREFIX}:{assignment_id}"


def prepare_worktree_handler(manager: GitWorktreeManager, store: Store) -> EffectHandler:
    """payload 形状：`{ticket_id, assignment_id, repository, base_ref}`（不含凭据，L7）。"""

    async def handle(effect: Effect) -> None:
        payload = effect.payload
        ticket = store.get_ticket(payload["ticket_id"])
        if ticket is None:
            raise ValueError(f"effect {effect.id} 的 Ticket {payload['ticket_id']!r} 不在库里")
        manager.prepare(ticket, repository=payload["repository"], base_ref=payload["base_ref"])

    return handle


def command_idempotency_key(assignment_id: str, sequence: int) -> str:
    """同一条命令默认只跑一次；`sequence` 是调用方给同一 Assignment 内命令的序号。"""
    return f"{COMMAND_KEY_PREFIX}:{assignment_id}:{sequence}"


def run_command_handler(
    runner: SandboxRunner,
    store: Store,
    *,
    clock: Clock | None = None,
    ids: IdFactory | None = None,
) -> EffectHandler:
    """把一次受限命令的结果写成 Assignment Report（规格 WP-5.2 第 3 条）。

    payload：`{ticket_id, assignment_id, argv, cwd, timeout_s?}`（不含凭据，L7）。身份
    （repository/base_ref）只从 Ticket 的 Mandate 取；`cwd` 交给 runner 的 `confine`——
    越界是 `cwd_escape` 结果而不是异常。Report 的正文就是 `CommandResult.summary()` 一行，
    details 是同批结构化字段；stdout/stderr 一个字符都不进（脱敏后的正文也只活在结果对象里）。

    重放范围按规格 2.5：命令的 I/O 在 claim 之后，报告写入与 effect 结算不同事务——崩溃重放
    可能让命令再跑一次并多出一行 Report；已确认的部分不在这条保证里。
    """
    clock = clock or SystemClock()
    ids = ids or Uuid4Id()

    async def handle(effect: Effect) -> None:
        payload = effect.payload
        ticket_id = payload.get("ticket_id")
        if not isinstance(ticket_id, str):
            raise PermanentEffectError("payload_missing:ticket_id")
        ticket = store.get_ticket(ticket_id)
        if ticket is None:
            raise PermanentEffectError(f"ticket_missing:{ticket_id}")
        mandate = store.get_mandate(ticket.mandate_id)
        if mandate is None:
            raise PermanentEffectError(f"mandate_missing:{ticket.mandate_id}")
        repository, base_ref = mandate.repository, mandate.base_ref
        if repository is None:
            raise PermanentEffectError("mandate_missing:repository")
        if base_ref is None:
            raise PermanentEffectError("mandate_missing:base_ref")
        assignment_id = payload.get("assignment_id")
        if not isinstance(assignment_id, str):
            raise PermanentEffectError("payload_missing:assignment_id")
        argv = payload.get("argv")
        if not isinstance(argv, list) or not all(isinstance(arg, str) for arg in argv):
            raise PermanentEffectError("payload_invalid:argv")
        cwd = payload.get("cwd")
        if not isinstance(cwd, str):
            raise PermanentEffectError("payload_missing:cwd")
        timeout_s = payload.get("timeout_s")
        if timeout_s is not None and not isinstance(timeout_s, int | float):
            raise PermanentEffectError("payload_invalid:timeout_s")

        result = await runner.run(
            CommandRequest(
                argv=tuple(argv),
                cwd=Path(cwd),
                context=CommandContext(
                    ticket_id=ticket.id,
                    assignment_id=assignment_id,
                    repository=repository,
                    base_ref=base_ref,
                ),
                timeout_s=None if timeout_s is None else float(timeout_s),
            )
        )
        store.insert_report(
            Report(
                id=ids.new_id("rp"),
                ticket_id=ticket.id,
                assignment_id=assignment_id,
                kind=(
                    ReportKind.BLOCKED if result.denied_reason is not None else ReportKind.PROGRESS
                ),
                summary=result.summary(),
                created_at=clock.now_ms(),
                details={
                    "mode": result.mode,
                    "exit_code": result.exit_code,
                    "denied_reason": result.denied_reason,
                    "timed_out": result.timed_out,
                    "truncated": result.truncated,
                    "duration_ms": result.ended_at - result.started_at,
                },
            )
        )

    return handle
