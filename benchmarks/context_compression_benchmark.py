"""P3 F1/F2：上下文压缩、工具爆炸和 token 校准的离线可复现基准。

该脚本不访问网络或真实 Provider。`reported_prompt_tokens` 是独立的、确定性 reference
计数器模拟 Provider usage，用于检验 P3 的估算误差计算管道，而不是宣称真实模型 BPE
精度。真实 Provider 的 usage 应作为部署后的补充测量单独记录。

固定条件：消息语料、事实、工具 schema、token 计数器和 relevance 规则均固定；压缩场景
只改变 compression 策略，工具爆炸场景只改变工具总量。输出为单个 JSON 文档，包含运行参数、
每种策略的 token/质量/校准结果，以及 resident 工具护栏的独立结果。

用法（仓库根目录）：

    python benchmarks/context_compression_benchmark.py --tools 200
    python benchmarks/context_compression_benchmark.py --tools 240 \
        --output docs/evidence/p3-context.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import tempfile
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, ClassVar

from stableagent.context.capability import RelevanceFilter
from stableagent.context.engine import ContextEngine
from stableagent.context.tokens import HeuristicTokenCounter, count_context
from stableagent.core.agent import Agent
from stableagent.core.checkpoint.recovery import migrate_state
from stableagent.core.checkpoint.storage import SqliteCheckpointStore
from stableagent.core.state import (
    AgentState,
    CheckpointConfig,
    Config,
    ContextConfig,
    Message,
    ToolCall,
)
from stableagent.providers.base import Provider, ProviderChunk, ProviderResponse
from stableagent.tools.registry import Tool, ToolRegistry, ToolResult

FACTS = tuple(f"FACT-{index}: verified finding {index}" for index in range(8))
COMPRESSION_STRATEGIES = ("none", "truncation", "summary", "hybrid")


class BenchmarkSummariser:
    """离线摘要桩：保留每条证据的前缀，使 hybrid 的摘录收益可被量化。"""

    def summarise(self, messages: list[Message]) -> str:
        return "\n".join(message.content[:260] for message in messages if message.content)


class CheckpointTool(Tool):
    """为 F5 构造稳定的大型工具结果，使摘要是否入快照可测。"""

    name = "read_checkpoint_finding"
    description = "read one long checkpoint finding"
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"index": {"type": "integer"}},
    }

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        index = int(arguments["index"])
        return ToolResult(output=f"FACT-{index}: {'payload ' * 80}")


class CheckpointProvider(Provider):
    """前四轮调用工具、第五轮结束；不访问网络。"""

    def __init__(self, turns: int = 5, *, completed_turns: int = 0) -> None:
        self._turns = turns
        self._calls = completed_turns

    def complete(
        self, messages: list[Message], tools: list[dict[str, Any]] | None = None, **options: Any
    ) -> ProviderResponse:
        self._calls += 1
        if self._calls >= self._turns:
            return ProviderResponse(content="checkpoint benchmark complete")
        return ProviderResponse(
            tool_calls=[
                ToolCall(
                    id=f"checkpoint-{self._calls}",
                    name=CheckpointTool.name,
                    arguments={"index": self._calls},
                )
            ]
        )

    async def acomplete(
        self, messages: list[Message], tools: list[dict[str, Any]] | None = None, **options: Any
    ) -> ProviderResponse:
        return self.complete(messages, tools, **options)

    async def stream(
        self, messages: list[Message], tools: list[dict[str, Any]] | None = None, **options: Any
    ) -> AsyncIterator[ProviderChunk]:
        raise NotImplementedError
        yield ProviderChunk(done=True)  # pragma: no cover


def build_corpus() -> AgentState:
    """构造固定的长任务语料：每条工具结果含一个最终答案需要的事实。"""
    messages = [
        Message(role="system", content="You are a careful research worker."),
        Message(
            role="user",
            content="Analyse the research findings and report every verified fact.",
        ),
    ]
    for index, fact in enumerate(FACTS):
        call = ToolCall(id=f"finding-{index}", name="read_finding", arguments={"index": index})
        messages.extend(
            [
                Message(
                    role="assistant",
                    content=f"I will inspect finding {index}.",
                    tool_calls=[call],
                ),
                Message(
                    role="tool",
                    name="read_finding",
                    tool_call_id=call.id,
                    content=(
                        f"{fact}. Supporting detail for deterministic benchmark corpus. "
                        f"evidence={'x' * 320}"
                    ),
                ),
            ]
        )
    return AgentState(run_id="p3-context-benchmark", agent_name="benchmark", messages=messages)


def base_specs() -> list[dict[str, Any]]:
    """固定小工具集，避免压缩测量与工具爆炸测量混杂变量。"""
    return [
        {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": {}},
        }
        for name, description in (
            ("read_finding", "read one verified research finding"),
            ("query_catalog", "query the research catalog"),
            ("render_report", "render a final research report"),
            ("save_report", "save the final research report"),
        )
    ]


def _reference_usage(messages: list[Message], specs: list[dict[str, Any]]) -> int:
    """模拟 Provider 回报的独立 token 口径，故意不同于启发式估算器。"""
    message_chars = sum(len(message.content) + len(message.role) for message in messages)
    spec_chars = len(json.dumps(specs, sort_keys=True, ensure_ascii=False, separators=(",", ":")))
    return math.ceil(message_chars / 3) + math.ceil(spec_chars / 5) + 2 * len(messages) + len(specs)


def _retention(messages: list[Message]) -> float:
    visible = "\n".join(message.content for message in messages)
    return sum(fact in visible for fact in FACTS) / len(FACTS)


def _compression_row(strategy: str) -> dict[str, Any]:
    state = build_corpus()
    specs = base_specs()
    config = ContextConfig(
        compression=strategy,
        keep_recent_messages=4,
        max_summary_chars=5_000,
        tool_result_excerpt_chars=80,
    )
    engine = ContextEngine(config, summariser=BenchmarkSummariser())
    folded = engine.fold(state)
    view = engine.assemble(folded.messages)
    counter = HeuristicTokenCounter()
    baseline = count_context(counter, state.messages, specs)
    estimate = engine.estimate(view, specs)
    reported = _reference_usage(view, specs)
    saving = baseline - estimate
    return {
        "strategy": strategy,
        "messages_before": len(state.messages),
        "messages_after": len(view),
        "estimated_prompt_tokens": estimate,
        "baseline_tokens": baseline,
        "estimated_tokens_saved": saving,
        "estimated_token_saving": round(saving / baseline, 4) if baseline else 0.0,
        "fact_retention": round(_retention(view), 4),
        "tool_pairs_intact": all(
            not message.tool_calls
            or all(
                next_message.role == "tool"
                for next_message in view[index + 1 : index + 1 + len(message.tool_calls)]
            )
            for index, message in enumerate(view)
        ),
        "calibration": {
            "reported_prompt_tokens": reported,
            "absolute_error": abs(estimate - reported),
            "mae": abs(estimate - reported),
            "signed_error": estimate - reported,
        },
    }


def explosion_specs(tool_count: int) -> list[dict[str, Any]]:
    if tool_count < 200:
        raise ValueError(f"tool_count must be at least 200 for P3 F2, got {tool_count}")
    specs = [
        {
            "name": f"utility_{index:03d}",
            "description": f"maintain unrelated fixture {index}",
            "parameters": {"type": "object", "properties": {"value": {"type": "string"}}},
        }
        for index in range(tool_count)
    ]
    specs[0] = {
        "name": "query_warehouse",
        "description": "query the analytics warehouse for research metrics",
        "parameters": {"type": "object", "properties": {}},
    }
    specs[1] = {
        "name": "search_research",
        "description": "search research findings and verified metrics",
        "parameters": {"type": "object", "properties": {}},
    }
    specs[-1] = {
        "name": "resident_audit",
        "description": "record an unrelated audit entry",
        "parameters": {"type": "object", "properties": {}},
    }
    return specs


def _tool_explosion_row(tool_count: int) -> dict[str, Any]:
    specs = explosion_specs(tool_count)
    resident = ToolCall(id="resident-call", name="resident_audit", arguments={})
    state = AgentState(
        run_id="p3-tool-explosion",
        agent_name="benchmark",
        messages=[
            Message(role="user", content="query research metrics in the analytics warehouse"),
            Message(role="assistant", content="", tool_calls=[resident]),
            Message(role="tool", name="resident_audit", tool_call_id=resident.id, content="done"),
        ],
    )
    config = ContextConfig(capability_filter="relevance", max_visible_tools=8)
    selected = RelevanceFilter(config).select(specs, state)
    report = RelevanceFilter(config)
    selected = report.select(specs, state)
    names = {str(spec["name"]) for spec in selected}
    full_tokens = count_context(HeuristicTokenCounter(), [], specs)
    return {
        "total_tools": len(specs),
        "tools_kept": len(selected),
        "full_tool_tokens": full_tokens,
        "dropped_tokens": report.last_report.dropped_tokens,
        "tool_token_saving": round(report.last_report.dropped_tokens / full_tokens, 4),
        "resident_preserved": "resident_audit" in names,
        "fallback": report.last_report.fallback,
        "selected_tools": sorted(names),
    }


async def _checkpoint_measurement(compression: str) -> dict[str, Any]:
    """模拟第 3 步崩溃后恢复，测量 fold 后 snapshot 的 payload 和读取恢复成本。"""
    with tempfile.TemporaryDirectory(prefix="stableagent-p3-") as directory:
        root = Path(directory)
        initial_path = root / "initial.sqlite"
        resumed_path = root / "resumed.sqlite"
        registry = ToolRegistry()
        registry.register(CheckpointTool())

        def config(path: Path) -> Config:
            return Config(
                checkpoint=CheckpointConfig(
                    backend="sqlite",
                    sqlite_path=str(path),
                    auto_save_every=1,
                    recovery_strategy="resume",
                    history_limit=20,
                ),
                context=ContextConfig(compression=compression, keep_recent_messages=2),
                control={"max_steps": 8},
            )

        initial = Agent(
            name="p3-checkpoint-benchmark",
            provider=CheckpointProvider(),
            registry=registry,
            config=config(initial_path),
        )
        state = await initial.arun("record checkpoint findings")
        history = await initial.checkpoint_history(state.run_id)
        boundary = next(record for record in history if record.step == 3)

        recovery_store = SqliteCheckpointStore(resumed_path)
        await recovery_store.save(boundary)
        recovery_store.close()
        resumed = Agent(
            name="p3-checkpoint-benchmark",
            provider=CheckpointProvider(completed_turns=3),
            registry=registry,
            config=config(resumed_path),
        )
        await resumed.arecover(state.run_id)
        records = await resumed.checkpoint_history(state.run_id)
        latest = records[-1]
        store = SqliteCheckpointStore(resumed_path)
        started = time.perf_counter()
        restored = await store.load(state.run_id)
        assert restored is not None
        migrate_state(restored.payload)
        recover_ms = (time.perf_counter() - started) * 1_000
        store.close()
        return {
            "checkpoint_count": len(records),
            "latest_payload_bytes": len(latest.payload.encode()),
            "recover_ms": round(recover_ms, 4),
        }


async def _checkpoint_impact() -> dict[str, dict[str, Any]]:
    return {
        "none": await _checkpoint_measurement("none"),
        "summary": await _checkpoint_measurement("summary"),
    }


def checkpoint_impact() -> dict[str, dict[str, Any]]:
    """同步边界，供 benchmark 测试与 CLI 调用；每次测量使用独立临时 SQLite。"""
    return asyncio.run(_checkpoint_impact())


def build_report(*, tool_count: int = 200) -> dict[str, Any]:
    """返回可用于测试、JSON 输出和文档证据的完整确定性报告。"""
    return {
        "event": "p3_context_benchmark",
        "meta": {
            "tool_count": tool_count,
            "history_turns": len(FACTS),
            "compression_keep_recent_messages": 4,
            "offline": True,
            "reported_usage": "deterministic synthetic provider reference; not a live-model claim",
        },
        "compression": [_compression_row(strategy) for strategy in COMPRESSION_STRATEGIES],
        "tool_explosion": _tool_explosion_row(tool_count),
        "checkpoint_impact": checkpoint_impact(),
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tools", type=int, default=200, help="工具爆炸场景工具数量，至少 200")
    parser.add_argument("--output", type=Path, help="可选 JSON 输出路径")
    args = parser.parse_args(argv)
    report = build_report(tool_count=args.tools)
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(f"{text}\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
