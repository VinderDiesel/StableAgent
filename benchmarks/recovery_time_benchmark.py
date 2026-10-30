"""P2 配套产出 G2：快照开销与恢复耗时基线（Roadmap 指标"恢复时间"，只对标自家基线）。

全部离线：Provider 是脚本化的（零网络、零凭据），存储走 SQLite 临时文件。
测量三件事，随 `auto_save_every` 变化：

1. `run_seconds` —— 同一个 N 步任务的端到端耗时（快照写入的摊薄开销 = 与 =0 的差值）
2. `write_ms`    —— 单次快照写入的平均耗时（含序列化 + sqlite 落盘）
3. `recover_ms`  —— 从"崩溃残留"到"可继续驱动的 AgentState"的耗时（load + 版本迁移）

用法（仓库根目录）：

    python benchmarks/recovery_time_benchmark.py --steps 20 --repeat 3

输出 JSON 行，可直接 `| jq` 或重定向存档；最后一行是 summary。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from stableagent.core.agent import Agent
from stableagent.core.checkpoint.recovery import migrate_state
from stableagent.core.checkpoint.storage import CheckpointRecord, SqliteCheckpointStore
from stableagent.core.state import AgentState, Config, Message, ToolCall
from stableagent.providers.base import Provider, ProviderResponse
from stableagent.tools.registry import Tool, ToolRegistry, ToolResult

BACKENDS = (0, 1, 2, 5)  # auto_save_every 取值；0 = 只在暂停/终止点落盘


class StepTool(Tool):
    name = "record_finding"
    description = "record one finding"
    parameters = {"type": "object", "properties": {"index": {"type": "integer"}}}

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        return ToolResult(output=f"finding #{arguments.get('index', 0)}")


class ScriptedProvider(Provider):
    """前 total-1 步调工具，最后一步收敛。零延时，好把存储开销凸显出来。"""

    def __init__(self, total: int) -> None:
        self._total = total
        self._step = 0

    def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        **options: Any,
    ) -> ProviderResponse:
        self._step += 1
        if self._step >= self._total:
            return ProviderResponse(content="完成")
        return ProviderResponse(
            content=f"第 {self._step} 步",
            tool_calls=[
                ToolCall(
                    id=f"c{self._step}", name="record_finding", arguments={"index": self._step}
                )
            ],
        )

    async def acomplete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        **options: Any,
    ) -> ProviderResponse:
        return self.complete(messages, tools, **options)

    async def stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        **options: Any,
    ) -> AsyncIterator[Any]:  # pragma: no cover - 基线不走流式
        raise NotImplementedError
        yield None


def build_agent(store_path: Path, auto_save_every: int, steps: int) -> Agent:
    registry = ToolRegistry()
    registry.register(StepTool())
    config = Config.model_validate(
        {
            "checkpoint": {
                "backend": "sqlite",
                "sqlite_path": str(store_path),
                "auto_save_every": auto_save_every,
                # resume 让 =0 这一档也有存储（=0 + none 会整体关闭持久化，见 build_store）
                "recovery_strategy": "resume",
                "history_limit": steps + 5,
            },
            "control": {"max_steps": steps + 5},
        }
    )
    return Agent(
        name="bench",
        provider=ScriptedProvider(steps),
        config=config,
        registry=registry,
    )


async def measure_run(store_path: Path, auto_save_every: int, steps: int) -> dict[str, Any]:
    agent = build_agent(store_path, auto_save_every, steps)
    started = time.perf_counter()
    events = [event async for event in agent.stream("基准任务")]
    run_seconds = time.perf_counter() - started
    records = await agent.checkpoint_history(agent.last_state.run_id)
    return {
        "auto_save_every": auto_save_every,
        "run_seconds": round(run_seconds, 4),
        "checkpoints_written": len(records),
        "checkpoint_events": sum(1 for e in events if e.type.value == "checkpoint"),
        "final_status": agent.last_state.status.value,
        "payload_bytes": sum(len(r.payload.encode()) for r in records),
    }


async def measure_write(store_path: Path, steps: int) -> dict[str, Any]:
    """隔离测单次写入：序列化 + 主键覆盖写 + 历史裁剪。"""
    store = SqliteCheckpointStore(store_path)
    state = AgentState(
        run_id="bench-write",
        agent_name="bench",
        step=steps,
        messages=[Message(role="user", content="q")] * steps,
    )
    durations: list[float] = []
    for step in range(1, steps + 1):
        record = CheckpointRecord.from_state(state.model_copy(update={"step": step}))
        started = time.perf_counter()
        await store.save(record)
        durations.append(time.perf_counter() - started)
    await store.delete("bench-write")
    store.close()
    return {
        "write_ms_mean": round(statistics.fmean(durations) * 1000, 3),
        "write_ms_p95": round(sorted(durations)[max(0, int(len(durations) * 0.95) - 1)] * 1000, 3),
        "serialize_ms": round(_serialize_cost(state) * 1000, 3),
    }


def _serialize_cost(state: AgentState) -> float:
    started = time.perf_counter()
    for _ in range(20):
        state.snapshot()
    return (time.perf_counter() - started) / 20


async def measure_recover(store_path: Path, steps: int) -> dict[str, Any]:
    """恢复耗时 = 读最近快照 + 版本迁移 + 反序列化成可驱动状态（不含续跑）。"""
    agent = build_agent(store_path, 1, steps)
    await agent.arun("基准任务")  # 留下终止态快照
    run_id = agent.last_state.run_id
    store = SqliteCheckpointStore(store_path)
    durations: list[float] = []
    for _ in range(10):
        started = time.perf_counter()
        record = await store.load(run_id)
        assert record is not None
        migrate_state(record.payload)
        durations.append(time.perf_counter() - started)
    await store.delete(run_id)
    store.close()
    return {
        "recover_ms_mean": round(statistics.fmean(durations) * 1000, 3),
        "recover_ms_max": round(max(durations) * 1000, 3),
        "history_steps": steps,
    }


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--steps", type=int, default=20, help="任务步数（默认 20）")
    parser.add_argument("--repeat", type=int, default=3, help="每个配置重复轮数（默认 3）")
    parser.add_argument("--dir", default=".bench-checkpoint", help="SQLite 目录（临时）")
    args = parser.parse_args(argv)

    root = Path(args.dir)
    root.mkdir(parents=True, exist_ok=True)
    print(json.dumps({"event": "meta", "steps": args.steps, "repeat": args.repeat}))

    medians: dict[int, list[float]] = {}
    for auto_save_every in BACKENDS:
        store_path = root / f"bench-a{auto_save_every}.sqlite"
        for _ in range(args.repeat):
            if store_path.exists():
                store_path.unlink()
            row = await measure_run(store_path, auto_save_every, args.steps)
            medians.setdefault(auto_save_every, []).append(row["run_seconds"])
            if store_path.exists():
                store_path.unlink()
        print(
            json.dumps(
                {
                    "event": "run",
                    "auto_save_every": auto_save_every,
                    "run_seconds_median": round(statistics.median(medians[auto_save_every]), 4),
                    **{k: v for k, v in row.items() if k != "auto_save_every"},
                },
                ensure_ascii=False,
            )
        )

    write = await measure_write(root / "bench-w.sqlite", args.steps)
    print(json.dumps({"event": "write", **write}))
    print(
        json.dumps(
            {"event": "recover", **(await measure_recover(root / "bench-r.sqlite", args.steps))},
        )
    )
    baseline = statistics.median(medians[0])
    summary = {
        "event": "summary",
        "baseline_auto_save_0_seconds": round(baseline, 4),
        "overhead_ratio": {
            str(every): round(statistics.median(values) / baseline, 3)
            for every, values in medians.items()
        },
        "note": "overhead_ratio = 该 auto_save_every 的端到端耗时 / 每步落盘次数最省的那一档",
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
