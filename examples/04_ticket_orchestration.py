"""P4 演示：父/子 Ticket + 依赖 + 两个脚本 Worker，离线跑完一条编排。

- A 是父 Ticket：两个工具步 + 终答；B 是子 Ticket：一个工具步 + 终答，`B depends_on A`。
- 两个 Worker 都是脚本化 Provider（按对话历史推断进度），零网络、零密钥。
- 事件按行输出 JSON；末行 `RUN_RESULT status=completed tickets=2`。
- A 到 READY 后由本示例代替人工验收调用 `complete_ticket`，B 的依赖闸门才打开。

用法：
    python examples/04_ticket_orchestration.py --phase run --dir .demo-orchestration

`--phase run` 会清空所选目录里的编排库与 checkpoint 库（演示目录，便于重跑得到确定性输出）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from stableagent.core.agent import Agent
from stableagent.core.state import Config, Message, ToolCall
from stableagent.orchestration.executor import AgentExecutor
from stableagent.orchestration.model import (
    Assignment,
    AssignmentAction,
    ReportKind,
    Ticket,
    TicketAction,
    TicketStatus,
)
from stableagent.orchestration.scheduler import Scheduler
from stableagent.orchestration.service import OrchestrationService
from stableagent.orchestration.store import Store
from stableagent.providers.base import Provider, ProviderChunk, ProviderResponse
from stableagent.tools.registry import Tool, ToolRegistry, ToolResult

PARENT_TASK = "父任务：调研 checkpoint 对 Agent 可用性的影响"
CHILD_TASK = "子任务：基于父任务发现写一份摘要"
TERMINAL_OR_STOPPED = frozenset(
    {
        TicketStatus.COMPLETED,
        TicketStatus.FAILED,
        TicketStatus.CANCELLED,
        TicketStatus.BLOCKED,
    }
)


class DemoClock:
    """每次 now_ms() 前进 1ms：due_at/退避在单进程内确定性推进，不读墙钟。"""

    def __init__(self, start: int = 1_700_000_000_000) -> None:
        self._now = start

    def now_ms(self) -> int:
        self._now += 1
        return self._now


class DemoIds:
    """seq-<tag>-<n>：重开库时新主键不与旧行相撞。"""

    def __init__(self) -> None:
        self._seq: dict[str, int] = {}

    def new_id(self, prefix: str) -> str:
        n = self._seq.get(prefix, 0) + 1
        self._seq[prefix] = n
        return f"{prefix}-{n:04d}"


class RecordFindingTool(Tool):
    """无副作用的占位工具：真实场景里这里是检索/写文件等昂贵操作。"""

    name = "record_finding"
    description = "record one finding of the ticket"
    parameters = {"type": "object", "properties": {"index": {"type": "integer"}}}

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        return ToolResult(output=f"finding #{arguments.get('index', 0)} recorded")


class ScriptedProvider(Provider):
    """按对话历史推断进度：快照里有 N 条 assistant 消息，本次就是第 N+1 步。

    前 tool_steps 步各调一次工具，之后收敛为终答——A 传 2、B 传 1。
    """

    def __init__(self, *, tool_steps: int, delay: float) -> None:
        self._tool_steps = tool_steps
        self._delay = delay

    def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        **options: Any,
    ) -> ProviderResponse:
        raise NotImplementedError("演示脚本只用异步形态")

    async def acomplete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        **options: Any,
    ) -> ProviderResponse:
        # 每步之间留出可观测窗口：selftest 靠"报告已落盘、下一步未开始"的间隙击杀进程。
        await asyncio.sleep(self._delay)
        step = sum(1 for message in messages if message.role == "assistant") + 1
        if step <= self._tool_steps:
            return ProviderResponse(
                content=f"第 {step} 步：先记录一条发现",
                tool_calls=[
                    ToolCall(id=f"c{step}", name="record_finding", arguments={"index": step})
                ],
                usage={"prompt_tokens": 40 * step, "completion_tokens": 5},
            )
        return ProviderResponse(
            content="工单完成：发现已记录",
            usage={"prompt_tokens": 40 * step, "completion_tokens": 10},
        )

    async def stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        **options: Any,
    ) -> AsyncIterator[ProviderChunk]:
        response = await self.acomplete(messages, tools=tools, **options)
        if response.content:
            yield ProviderChunk(text=response.content)
        for call in response.tool_calls:
            yield ProviderChunk(tool_call=call)
        yield ProviderChunk(done=True)


class PrintingSink:
    """Delegate to the service, then print the persisted report as a JSON line."""

    def __init__(self, service: OrchestrationService, store: Store) -> None:
        self._service = service
        self._store = store

    def add_report(
        self,
        *,
        ticket_id: str,
        assignment_id: str,
        kind: ReportKind,
        summary: str,
        event_step: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self._service.add_report(
            ticket_id=ticket_id,
            assignment_id=assignment_id,
            kind=kind,
            summary=summary,
            event_step=event_step,
            details=details,
        )
        report = self._store.list_reports(ticket_id)[-1]
        print(
            json.dumps(
                {
                    "report": report.id,
                    "ticket": ticket_id,
                    "assignment": assignment_id,
                    "kind": report.kind.value,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    def register_run_id(self, assignment_id: str, worker_run_id: str) -> None:
        self._service.register_run_id(assignment_id, worker_run_id)

    def finish_assignment(
        self, assignment_id: str, action: AssignmentAction, *, failure: str | None = None
    ) -> None:
        self._service.finish_assignment(assignment_id, action, failure=failure)

    def transition_ticket(self, ticket_id: str, action: TicketAction) -> None:
        self._service.transition_ticket(ticket_id, action)

    def mark_input_consumed(self, ticket_id: str, input_id: str) -> None:
        self._service.mark_input_consumed(ticket_id, input_id)


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(RecordFindingTool())
    return registry


def build(workdir: Path, *, delay: float) -> tuple[Store, OrchestrationService, Scheduler]:
    clock = DemoClock()
    store = Store(workdir / "orchestration.db", clock=clock)
    service = OrchestrationService(store, clock=clock, ids=DemoIds())
    sink = PrintingSink(service, store)
    registry = _registry()

    def factory(ticket: Ticket, assignment: Assignment) -> Agent:
        return Agent(
            name=f"worker-{ticket.id}",
            provider=ScriptedProvider(tool_steps=2 if ticket.parent_id is None else 1, delay=delay),
            config=Config(
                checkpoint={
                    "backend": "sqlite",
                    "sqlite_path": str(workdir / "checkpoint.db"),
                    "auto_save_every": 1,
                    "recovery_strategy": "resume",
                }
            ),
            registry=registry,
        )

    executor = AgentExecutor(sink=sink, agent_factory=factory, clock=clock)
    scheduler = Scheduler(
        store=store, service=service, executor=executor, clock=clock, max_concurrency=2
    )
    return store, service, scheduler


async def drive_until_idle(
    service: OrchestrationService, scheduler: Scheduler, *, max_rounds: int = 2_000
) -> list[Ticket]:
    """轮询 tick；READY 票代替人工验收 complete；全部收口或受阻即返回。"""
    for _ in range(max_rounds):
        await scheduler.drain(max_rounds=8)
        for view in service.list_tickets(status=TicketStatus.READY):
            service.complete_ticket(view.ticket.id, summary="人工验收通过")
        views = service.list_tickets()
        if (
            all(view.ticket.status in TERMINAL_OR_STOPPED for view in views)
            and not scheduler.running_assignments()
        ):
            return [view.ticket for view in views]
        await asyncio.sleep(0.01)
    raise RuntimeError("驱动超时：Ticket 没有收敛")


async def phase_run(workdir: Path, *, delay: float) -> int:
    # 演示目录：每次 run 都从空库开始，保证 tk-0001/tk-0002 这类确定性输出。
    (workdir / "orchestration.db").unlink(missing_ok=True)
    (workdir / "checkpoint.db").unlink(missing_ok=True)
    store, service, scheduler = build(workdir, delay=delay)
    try:
        parent = service.create_ticket(PARENT_TASK)
        child = service.create_ticket(CHILD_TASK, parent_id=parent.id)
        service.add_dependency(child.id, parent.id)
        service.queue_ticket(parent.id)
        service.queue_ticket(child.id)

        tickets = await drive_until_idle(service, scheduler)
        for ticket in tickets:
            print(json.dumps({"ticket": ticket.id, "status": ticket.status.value}), flush=True)
        completed = sum(1 for ticket in tickets if ticket.status is TicketStatus.COMPLETED)
        ok = len(tickets) == 2 and completed == 2
        print(
            f"RUN_RESULT status={'completed' if ok else 'failed'} tickets={len(tickets)}",
            flush=True,
        )
        return 0 if ok else 1
    finally:
        store.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="P4 父子 Ticket 编排演示")
    parser.add_argument("--phase", choices=["run"], default="run")
    parser.add_argument("--dir", default=".demo-orchestration")
    parser.add_argument("--delay", type=float, default=0.15)
    args = parser.parse_args()

    workdir = Path(args.dir)
    workdir.mkdir(parents=True, exist_ok=True)
    raise SystemExit(asyncio.run(phase_run(workdir, delay=args.delay)))


if __name__ == "__main__":
    main()
