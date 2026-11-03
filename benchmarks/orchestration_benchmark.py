"""P4 编排基准：固定 50 Ticket / 100 Assignment 下的 tick / recover / drain 量级。

全部离线、确定性：注入 `BenchClock`/`BenchIds`，Worker 用零延时 `InstantExecutor`。
只报本机量级，不改任何生产超时，也不外推为性能承诺（账本 H7）。

数据形状：每 Ticket 两条 Assignment 历史——attempt 1 已 ENDED，attempt 2 由 queue 产生。
Phase A 记单轮 `tick()`（enqueue + dispatch 窗口）；Phase B 构造"崩溃现场"（最新 Assignment
与 Ticket 均为 RUNNING），记 `recover()` 与 `drain()` 到 effect 全部重放的耗时。

用法（仓库根目录）：

    python benchmarks/orchestration_benchmark.py --tickets 50 --assignments 100 \
      --repeat 3 --dir .bench-p4-orchestration

输出每 phase 一行 JSON；末行 summary 含 min/median/max。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import statistics
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from stableagent.orchestration.model import (
    Assignment,
    AssignmentAction,
    AssignmentStatus,
    Ticket,
    TicketAction,
    TicketInput,
    TicketStatus,
)
from stableagent.orchestration.scheduler import Scheduler
from stableagent.orchestration.service import OrchestrationService
from stableagent.orchestration.store import Store


class BenchClock:
    """每次 now_ms() 前进 1ms：due_at/退避在单进程内确定性推进，不读墙钟。"""

    def __init__(self, start: int = 1_700_000_000_000) -> None:
        self._now = start

    def now_ms(self) -> int:
        self._now += 1
        return self._now


class BenchIds:
    def __init__(self) -> None:
        self._seq: dict[str, int] = {}

    def new_id(self, prefix: str) -> str:
        n = self._seq.get(prefix, 0) + 1
        self._seq[prefix] = n
        return f"{prefix}-{n:05d}"


class InstantExecutor:
    """零延时桩：launch/recover 即收口，把测量收敛到 Store + Scheduler 的开销。"""

    def __init__(self, service: OrchestrationService) -> None:
        self._service = service
        self.launches = 0
        self.recoveries = 0

    async def launch(
        self, assignment: Assignment, ticket: Ticket, inputs: Sequence[TicketInput]
    ) -> None:
        self.launches += 1
        self._service.finish_assignment(assignment.id, AssignmentAction.END)
        self._service.transition_ticket(ticket.id, TicketAction.DELIVER)

    async def recover(self, assignment: Assignment, ticket: Ticket) -> None:
        self.recoveries += 1
        self._service.finish_assignment(assignment.id, AssignmentAction.END)
        self._service.transition_ticket(ticket.id, TicketAction.DELIVER)

    async def deliver(
        self, assignment: Assignment, ticket: Ticket, inputs: Sequence[TicketInput]
    ) -> None:  # pragma: no cover - 基准不投递输入
        raise NotImplementedError

    async def cancel(self, assignment: Assignment) -> None:  # pragma: no cover
        return


def build(root: Path, *, tickets: int) -> tuple[Store, OrchestrationService, Scheduler, BenchClock]:
    clock = BenchClock()
    store = Store(root / "orchestration.db", clock=clock)
    service = OrchestrationService(store, clock=clock, ids=BenchIds())
    executor = InstantExecutor(service)
    scheduler = Scheduler(
        store=store,
        service=service,
        executor=executor,
        clock=clock,
        max_concurrency=tickets,
    )
    return store, service, scheduler, clock


def seed(store: Store, service: OrchestrationService, clock: BenchClock, *, tickets: int) -> None:
    """每 Ticket：attempt 1 直接落 ENDED 作为历史，queue 建 attempt 2 + launch effect。"""
    for index in range(tickets):
        ticket = service.create_ticket(f"bench ticket {index}")
        now = clock.now_ms()
        with store.transaction():
            store.insert_assignment(
                Assignment(
                    id=f"as-history-{index:05d}",
                    ticket_id=ticket.id,
                    attempt=1,
                    status=AssignmentStatus.ENDED,
                    started_at=now,
                    ended_at=now,
                )
            )
        service.queue_ticket(ticket.id)


def force_crash_shape(store: Store) -> None:
    """把最新 Assignment 与 Ticket 都置 RUNNING：模拟"effect 未重放完就被 SIGKILL"。"""
    with store.transaction():
        for ticket in store.list_tickets():
            latest = store.list_assignments(ticket.id)[-1]
            if latest.status is not AssignmentStatus.RUNNING:
                store.update_assignment(
                    latest.model_copy(update={"status": AssignmentStatus.RUNNING, "ended_at": None})
                )
            if ticket.status is not TicketStatus.RUNNING:
                store.update_ticket(
                    ticket.model_copy(update={"status": TicketStatus.RUNNING, "ended_at": None})
                )


async def measure_once(root: Path, *, tickets: int) -> dict[str, float]:
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    store, service, scheduler, clock = build(root, tickets=tickets)
    try:
        seed(store, service, clock, tickets=tickets)

        started = time.perf_counter()
        await scheduler.tick()
        tick_ms = (time.perf_counter() - started) * 1000

        force_crash_shape(store)
        started = time.perf_counter()
        service.recover()
        recover_ms = (time.perf_counter() - started) * 1000

        started = time.perf_counter()
        await scheduler.drain(max_rounds=200)
        drain_ms = (time.perf_counter() - started) * 1000
    finally:
        store.close()
    return {"tick_ms": tick_ms, "recover_ms": recover_ms, "drain_ms": drain_ms}


def _stats(values: list[float]) -> dict[str, float]:
    return {
        "min": round(min(values), 3),
        "median": round(statistics.median(values), 3),
        "max": round(max(values), 3),
    }


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tickets", type=int, default=50, help="Ticket 数（默认 50）")
    parser.add_argument(
        "--assignments", type=int, default=100, help="Assignment 总数，必须为 Ticket 数的 2 倍"
    )
    parser.add_argument("--repeat", type=int, default=3, help="重复轮数（默认 3）")
    parser.add_argument("--dir", default=".bench-p4-orchestration", help="SQLite 目录（临时）")
    args = parser.parse_args(argv)

    if args.tickets < 1 or args.assignments != args.tickets * 2 or args.repeat < 1:
        parser.error("要求 tickets >= 1、assignments == 2 * tickets、repeat >= 1")

    root = Path(args.dir)
    root.mkdir(parents=True, exist_ok=True)
    print(
        json.dumps(
            {
                "event": "meta",
                "tickets": args.tickets,
                "assignments": args.assignments,
                "repeat": args.repeat,
            }
        ),
        flush=True,
    )

    samples: dict[str, list[float]] = {"tick_ms": [], "recover_ms": [], "drain_ms": []}
    for run in range(args.repeat):
        row = await measure_once(root / f"run-{run:02d}", tickets=args.tickets)
        for key in samples:
            samples[key].append(row[key])
        print(json.dumps({"event": "run", "repeat": run, **row}), flush=True)

    tick = _stats(samples["tick_ms"])
    recover = _stats(samples["recover_ms"])
    drain = _stats(samples["drain_ms"])
    summary: dict[str, Any] = {
        "event": "summary",
        "tickets": args.tickets,
        "assignments": args.assignments,
        "repeat": args.repeat,
        "tick_ms": tick["median"],
        "tick_ms_min": tick["min"],
        "tick_ms_max": tick["max"],
        "recover_ms": recover["median"],
        "recover_ms_min": recover["min"],
        "recover_ms_max": recover["max"],
        "drain_ms": drain["median"],
        "drain_ms_min": drain["min"],
        "drain_ms_max": drain["max"],
        "note": "本机量级，不外推为性能承诺",
    }
    print(json.dumps({"summary": summary}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
