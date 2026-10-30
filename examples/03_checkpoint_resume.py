"""P2 演示：运行中 kill 进程 → 重启 → 从最近 checkpoint 恢复并跑完。

这是 Roadmap P2 的招牌动作，全部离线、确定性，不需要任何模型凭据：
Provider 是脚本化的（按快照里已有的 assistant 消息数决定下一步），每步之间加入可配置的
慢速延时，好让外部进程有把握在"快照已落盘、下一步还没开始"的窗口里把它杀掉。

用法（同一目录，三步）：

1. 跑（正常会跑完；外部 kill 请见第 3 步）
   python examples/03_checkpoint_resume.py --phase run --dir .demo-checkpoint
2. 恢复（不给 run_id 就取该目录里最近一次未完成的运行）
   python examples/03_checkpoint_resume.py --phase resume --dir .demo-checkpoint
3. 一键自测：起子进程 → 看到 step=2 的 checkpoint 就 SIGKILL → 恢复并跑完 → 退出码 0
   python examples/03_checkpoint_resume.py --phase selftest --dir .demo-checkpoint

事件按行输出 JSON；`checkpoint` 行里的 run_id/step 就是恢复坐标。
恢复阶段末行输出 `RESUME_RESULT status=... steps=... provider_calls=...`。
`provider_calls` 恰等于崩溃后剩余的步数（本设计 TOTAL_STEPS - KILL_AFTER_STEP = 2），
这就是"已完成步不重跑"的直接证据。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sqlite3
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from stableagent.core.agent import Agent
from stableagent.core.checkpoint.storage import SqliteCheckpointStore
from stableagent.core.state import Config, EventType, Message, ToolCall
from stableagent.providers.base import Provider, ProviderChunk, ProviderResponse
from stableagent.tools.registry import Tool, ToolRegistry, ToolResult

TOTAL_STEPS = 4  # 第 1~3 步调工具，第 4 步收敛
# 击杀点之后必须还剩至少一次 provider 往返：回看式落盘让第 k 步的 checkpoint 与第 k+1 步
# 的首个事件同时出现，若 k+1 就是终止步，父进程的 SIGKILL 会和子进程正常退出赛跑。
KILL_AFTER_STEP = 2  # selftest 在第 2 步快照落盘后击杀


class RecordTool(Tool):
    """无副作用的占位工具：真实场景里这里是检索/代码执行等昂贵操作。"""

    name = "record_finding"
    description = "record one finding of the research task"
    parameters = {"type": "object", "properties": {"index": {"type": "integer"}}}

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        index = arguments.get("index", 0)
        return ToolResult(output=f"finding #{index} recorded")


class ScriptedSlowProvider(Provider):
    """按对话历史推断进度：快照里有 N 条 assistant 消息，本次就是第 N+1 步。

    进度信息只存在于快照里，所以崩溃重启后不会把已完成的步重跑一遍。
    """

    def __init__(self, *, delay: float = 0.6) -> None:
        self._delay = delay
        self.calls = 0

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
        self.calls += 1
        await asyncio.sleep(self._delay)
        step = sum(1 for message in messages if message.role == "assistant") + 1
        if step >= TOTAL_STEPS:
            return ProviderResponse(
                content=f"研究完成：共 {TOTAL_STEPS - 1} 条发现",
                usage={"prompt_tokens": 50 * step, "completion_tokens": 10},
            )
        return ProviderResponse(
            content=f"第 {step} 步：先记录一条发现",
            tool_calls=[ToolCall(id=f"c{step}", name="record_finding", arguments={"index": step})],
            usage={"prompt_tokens": 40 * step, "completion_tokens": 5},
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


def build_agent(db_path: Path, *, delay: float) -> tuple[Agent, ScriptedSlowProvider]:
    registry = ToolRegistry()
    registry.register(RecordTool())
    provider = ScriptedSlowProvider(delay=delay)
    config = Config.model_validate(
        {
            "checkpoint": {
                "backend": "sqlite",
                "sqlite_path": str(db_path),
                "auto_save_every": 1,  # 每步落盘：崩溃最多损失一步
                "recovery_strategy": "resume",
            },
            "control": {"max_steps": 10},
        }
    )
    agent = Agent(
        name="crash-recovery-demo",
        provider=provider,
        config=config,
        registry=registry,
        checkpoint_store=SqliteCheckpointStore(db_path),
    )
    return agent, provider


def emit(event: Any) -> None:
    print(json.dumps(event.model_dump(mode="json"), ensure_ascii=False), flush=True)


async def phase_run(db_path: Path, *, delay: float) -> int:
    agent, _ = build_agent(db_path, delay=delay)
    async for event in agent.stream("调研 checkpoint 对 Agent 可用性的影响"):
        emit(event)
    state = agent.last_state
    assert state is not None
    print(f"RUN_RESULT status={state.status.value} steps={state.step}", flush=True)
    return 0 if state.status.value == "succeeded" else 1


async def phase_resume(db_path: Path, run_id: str | None, *, delay: float) -> int:
    agent, provider = build_agent(db_path, delay=delay)
    target = run_id or await _unfinished_run_id(db_path)
    if target is None:
        print("没有未完成的快照可恢复（先跑 --phase run 并被 kill）", file=sys.stderr)
        return 1
    async for event in agent.stream_recover(target):
        emit(event)
    state = agent.last_state
    assert state is not None
    print(
        f"RESUME_RESULT status={state.status.value} steps={state.step} "
        f"provider_calls={provider.calls}",
        flush=True,
    )
    return 0 if state.status.value == "succeeded" else 1


async def _unfinished_run_id(db_path: Path) -> str | None:
    """最近一次尚未成功的 run（同库可能有多个 run，按 saved_at 倒序找）。"""
    store = SqliteCheckpointStore(db_path)

    def _rows() -> list[tuple[str, str]]:
        conn = sqlite3.connect(store.path)
        try:
            return conn.execute(
                "SELECT run_id, payload FROM checkpoints ORDER BY saved_at DESC"
            ).fetchall()
        finally:
            conn.close()

    for run_id, payload in await asyncio.to_thread(_rows):
        state = json.loads(payload)
        if state["status"] not in ("succeeded", "failed"):
            return str(run_id)
    return None


async def phase_selftest(workdir: Path, *, delay: float) -> int:
    """起子进程跑 run → 看见第 KILL_AFTER_STEP 步 checkpoint 就 SIGKILL → 恢复跑完。"""
    db_path = workdir / "checkpoints.sqlite"
    db_path.unlink(missing_ok=True)
    script = Path(__file__).resolve()
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        str(script),
        "--phase",
        "run",
        "--dir",
        str(workdir),
        "--delay",
        str(delay),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    assert proc.stdout is not None
    run_id = ""
    killed = False
    while True:
        line_bytes = await proc.stdout.readline()
        if not line_bytes:
            break
        line = line_bytes.decode().rstrip()
        print(f"[run] {line}", flush=True)
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        data = payload.get("data") or {}
        if payload.get("type") != EventType.CHECKPOINT.value:
            continue
        run_id = str(data.get("run_id", run_id))
        if int(data.get("step", 0)) >= KILL_AFTER_STEP and not killed and proc.pid:
            os.kill(proc.pid, signal.SIGKILL)
            killed = True
    exit_code = await proc.wait()
    if not killed:
        print("演示进程自己跑完了，没给 kill 留出窗口（把 --delay 调大）", file=sys.stderr)
        return 1
    if exit_code == 0:
        print(f"被 kill 的进程退出码为 0，不符合预期：{exit_code}", file=sys.stderr)
        return 1

    resume_code = await phase_resume(db_path, run_id, delay=0.0)
    store = SqliteCheckpointStore(db_path)
    record = await store.load(run_id)
    final = json.loads(record.payload) if record else {}
    ok = (
        resume_code == 0 and final.get("status") == "succeeded" and final.get("step") == TOTAL_STEPS
    )
    print(f"SELFTEST ok={ok} killed=True final={final.get('status')}", flush=True)
    return 0 if ok else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="P2 崩溃恢复演示")
    parser.add_argument("--phase", choices=["run", "resume", "selftest"], default="run")
    parser.add_argument("--dir", default=".demo-checkpoint")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--delay", type=float, default=0.6)
    args = parser.parse_args()

    workdir = Path(args.dir)
    workdir.mkdir(parents=True, exist_ok=True)
    db_path = workdir / "checkpoints.sqlite"

    if args.phase == "run":
        raise SystemExit(asyncio.run(phase_run(db_path, delay=args.delay)))
    if args.phase == "resume":
        raise SystemExit(asyncio.run(phase_resume(db_path, args.run_id, delay=args.delay)))
    raise SystemExit(asyncio.run(phase_selftest(workdir, delay=args.delay)))


if __name__ == "__main__":
    main()
