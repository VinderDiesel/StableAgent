"""M1 崩溃演练的子进程入口：--phase run|recover，逐行打印 JSON 事件。

与 examples/03_checkpoint_resume.py 走同一套约定：stdout 每行一个 JSON，测试据此决定击杀时机。
Worker 用 tests.support.FakeProvider（离线、确定性），绝不碰网络。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from stableagent.core.agent import Agent
from stableagent.core.state import Config
from stableagent.orchestration.executor import AgentExecutor
from stableagent.orchestration.model import (
    Assignment,
    AssignmentAction,
    ReportKind,
    Ticket,
    TicketAction,
)
from stableagent.orchestration.scheduler import Scheduler
from stableagent.orchestration.service import OrchestrationService
from stableagent.orchestration.store import Store
from stableagent.tools.registry import ToolRegistry
from tests.support import EchoTool, FakeProvider, ProviderResponse, tool_response


class _AdvanceClock:
    """每次 now_ms() 前进 1000ms：due_at/backoff 判定在单进程内确定性推进，不读墙钟。"""

    def __init__(self, start: int = 1_700_000_000_000) -> None:
        self._now = start

    def now_ms(self) -> int:
        self._now += 1_000
        return self._now


class _PrefixedIds:
    """带进程标签的序号 ID：同一 DB 被 recover 进程重开时，新行主键不与旧行相撞。"""

    def __init__(self, tag: str) -> None:
        self._tag = tag
        self._n = 0

    def new_id(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}-{self._tag}-{self._n:04d}"


class SlowProvider(FakeProvider):
    """每次调用前真实 sleep：拉大"报告已落库、终态步未开始"的窗口（同 P2 的 delay 做法）。

    注意 loop 走的是 acomplete（loop.py:63），不是 stream——晚 sleep 打在报告之后的下一次调用上。
    """

    def __init__(self, responses: list[ProviderResponse], delay: float) -> None:
        super().__init__(responses)
        self._delay = delay

    async def acomplete(  # type: ignore[override]
        self,
        messages: Any,
        tools: Any = None,
        **options: Any,
    ) -> ProviderResponse:
        await asyncio.sleep(self._delay)
        return await super().acomplete(messages, tools, **options)


class PrintingSink:
    """AssignmentSink 装饰器：委托 service 落库后把报告打成 JSON 行，给测试确定性击杀点。

    打印发生在 add_report 事务提交之后——报告行出现在 stdout 时，它已经持久化。
    """

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
                {"report": report.id, "assignment": assignment_id, "kind": report.kind.value}
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
    registry.register(EchoTool())
    return registry


def _build(db: Path, clock: _AdvanceClock, tag: str, built: list[SlowProvider]):
    store = Store(db / "orchestration.db", clock=clock)
    ids = _PrefixedIds(tag)
    service = OrchestrationService(store, clock=clock, ids=ids)

    def factory(ticket: Ticket, assignment: Assignment) -> Agent:
        # Core 的惰性落盘在"下一个 step 变化"时才把 pending 状态写快照，TOOL_CALL 与 TOOL_RESULT
        # 同属一个 step——所以在 B 的第 2 个 progress 报告上击杀时，被杀 Worker 必须已经跨过两个
        # 工具步，第一个步的快照才存在。A 没有工具步：只出一个终态 ready 报告、直接 ended，
        # 用作"已完成不重跑"的对照。
        responses = (
            [_final("完成：见报告")]
            if ticket.task.endswith("A")
            else [
                tool_response("call-1", "第 1 步中间结果"),
                tool_response("call-2", "第 2 步中间结果"),
                _final("完成：见报告"),
            ]
        )
        provider = SlowProvider(responses, delay=0.5)
        built.append(provider)
        return Agent(
            name=f"worker-{assignment.attempt}",
            provider=provider,
            config=Config(
                checkpoint={
                    "backend": "sqlite",
                    "sqlite_path": str(db / "checkpoint.db"),
                    "auto_save_every": 1,
                    "recovery_strategy": "resume",
                }
            ),
            registry=_registry(),
        )

    return store, service, factory


def _final(text: str) -> ProviderResponse:
    from stableagent.providers.base import ProviderResponse as PR

    return PR(content=text)


async def _drain_to_idle(scheduler: Scheduler) -> None:
    """drain 的 sleep(0) 推不动真实 sleep 的 Worker：轮询直到队列与在途任务都清空。"""
    for _ in range(2_000):
        await scheduler.drain(max_rounds=8)
        if not scheduler.running_assignments():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("drain 超时：Worker 没有收敛")


async def run(phase: str, db: Path) -> None:
    clock = _AdvanceClock()
    built: list[SlowProvider] = []
    store, service, factory = _build(db, clock, phase, built)
    sink = PrintingSink(service, store)
    executor = AgentExecutor(sink=sink, agent_factory=factory, clock=clock)
    scheduler = Scheduler(
        store=store, service=service, executor=executor, clock=clock, max_concurrency=1
    )

    if phase == "run":
        first = service.queue_ticket(service.create_ticket("任务 A").id)
        second = service.queue_ticket(service.create_ticket("任务 B").id)
        await _drain_to_idle(scheduler)
        for ticket in (first, second):
            print(
                json.dumps(
                    {"ticket": ticket.id, "status": store.get_ticket(ticket.id).status.value}
                ),
                flush=True,
            )
    else:
        summary = service.recover()
        print(json.dumps({"recover": summary.__dict__}), flush=True)
        await _drain_to_idle(scheduler)
        for view in service.list_tickets():
            print(
                json.dumps({"ticket": view.ticket.id, "status": view.ticket.status.value}),
                flush=True,
            )
        # M1.9 的"Provider 调用次数硬断言"数据源：恢复进程里每建一个 Worker 记一条。
        print(json.dumps({"provider_calls": [len(p.calls) for p in built]}), flush=True)
    store.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["run", "recover"], required=True)
    parser.add_argument("--dir", required=True)
    args = parser.parse_args()
    asyncio.run(run(args.phase, Path(args.dir)))


if __name__ == "__main__":
    main()
