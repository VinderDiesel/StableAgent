"""stdlib CLI 入口：只解析参数、调用 Console、映射退出码（规格 WP-6.5）。"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from stableagent.control.errors import ApprovalDecidedError, UnknownApprovalError
from stableagent.orchestration.errors import UnknownTicketError
from stableagent.orchestration.model import SystemClock, Uuid4Id
from stableagent.supervisor.console import (
    CliError,
    Console,
    ConsoleConfig,
    render_approval,
    render_control,
    render_decision,
    render_retry,
    render_status,
    render_thread,
    render_ticket,
)
from stableagent.supervisor.router import RouterError, RouterPermissionError
from stableagent.supervisor.store import SupervisorStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m stableagent.supervisor.cli",
        description="Supervisor 监管控制台（只读首屏 + 显式确认写命令）",
    )
    parser.add_argument("--db", required=True, help="SQLite 路径")
    parser.add_argument("--config", required=True, help="JSON 配置路径")
    parser.add_argument("--operator", required=True, help="执行人（大小写不敏感）")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="首屏：注意力、Source 健康、队列与运行态")
    ticket = commands.add_parser("ticket", help="Ticket 详情与完整时间线")
    ticket.add_argument("ticket_id")
    thread = commands.add_parser("thread", help="Thread 时间线")
    thread.add_argument("thread_key")

    approve = commands.add_parser("approve", help="批准一条待审事项")
    approve.add_argument("approval_id")
    approve.add_argument("--confirm", required=True)
    deny = commands.add_parser("deny", help="拒绝一条待审事项")
    deny.add_argument("approval_id")
    deny.add_argument("--confirm", required=True)
    stop = commands.add_parser("stop", help="停止一个 Ticket")
    stop.add_argument("ticket_id")
    stop.add_argument("--confirm", required=True)
    stop.add_argument("--reason")
    retry = commands.add_parser("retry", help="重试失败 effect 或 FAILED Ticket")
    retry.add_argument("target_id")
    retry.add_argument("--confirm", required=True)
    reevaluate = commands.add_parser("reevaluate", help="重评估一条 Trigger")
    reevaluate.add_argument("trigger_id")
    reevaluate.add_argument("--confirm", required=True)
    pause = commands.add_parser("pause", help="暂停处理")
    pause.add_argument("--confirm", required=True)
    pause.add_argument("--reason")
    resume = commands.add_parser("resume", help="恢复处理")
    resume.add_argument("--confirm", required=True)
    resume.add_argument("--reason")
    return parser


def _load_config(path: str) -> ConsoleConfig:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CliError(1, f"配置读取失败：{error}") from error
    if not isinstance(raw, dict):
        raise CliError(1, "配置必须是 JSON 对象")
    try:
        return ConsoleConfig.model_validate(raw)
    except ValidationError as error:
        raise CliError(1, f"配置无效：{error}") from error


def _run(console: Console, args: argparse.Namespace) -> str:
    if args.command == "status":
        return render_status(console.status())
    if args.command == "ticket":
        return render_ticket(console.ticket(args.ticket_id))
    if args.command == "thread":
        return render_thread(console.thread(args.thread_key))
    if args.command == "approve":
        return render_approval(
            console.approve(args.approval_id, actor=args.operator, confirm=args.confirm),
            approved=True,
        )
    if args.command == "deny":
        return render_approval(
            console.deny(args.approval_id, actor=args.operator, confirm=args.confirm),
            approved=False,
        )
    if args.command == "stop":
        ticket = console.stop(
            args.ticket_id,
            actor=args.operator,
            confirm=args.confirm,
            reason=args.reason,
        )
        return f"stopped {ticket.id} status={ticket.status.value}"
    if args.command == "retry":
        return render_retry(
            console.retry(args.target_id, actor=args.operator, confirm=args.confirm)
        )
    if args.command == "reevaluate":
        return render_decision(
            console.reevaluate(args.trigger_id, actor=args.operator, confirm=args.confirm)
        )
    if args.command == "pause":
        return render_control(
            console.pause(actor=args.operator, confirm=args.confirm, reason=args.reason)
        )
    if args.command == "resume":
        return render_control(
            console.resume(actor=args.operator, confirm=args.confirm, reason=args.reason)
        )
    raise CliError(1, f"未知命令：{args.command}")


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exit_error:
        return int(exit_error.code or 0)
    clock = SystemClock()
    store: SupervisorStore | None = None
    try:
        config = _load_config(args.config)
        store = SupervisorStore(args.db, clock=clock)
        console = Console(store, clock=clock, ids=Uuid4Id(), config=config)
        output = _run(console, args)
    except RouterPermissionError as error:
        print(f"[4] {error.detail}", file=sys.stderr)
        return 4
    except CliError as error:
        print(str(error), file=sys.stderr)
        return error.code
    except (
        RouterError,
        UnknownApprovalError,
        ApprovalDecidedError,
        UnknownTicketError,
        KeyError,
    ) as error:
        print(f"[1] {error}", file=sys.stderr)
        return 1
    finally:
        if store is not None:
            store.close()
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
