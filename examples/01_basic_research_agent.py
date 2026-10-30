"""P1 真实端到端示例（DoD A1）：多步研究任务，事件流完整打印。

需要 OPENAI_API_KEY 环境变量；离线演示请用 02_loop_offline_demo.py。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from stableagent import Agent, Event, EventType, OpenAIProvider
from stableagent.core.state import Config
from stableagent.tools.registry import Tool, ToolRegistry, ToolResult

CORPUS_DIR = Path(__file__).parent / "data"


class ListCorpusFiles(Tool):
    name = "list_corpus_files"
    description = "列出本地研究语料目录中的所有 Markdown 文件名"
    parameters = {"type": "object", "properties": {}}

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        files = sorted(p.name for p in CORPUS_DIR.glob("*.md"))
        return ToolResult(output=json.dumps(files, ensure_ascii=False))


class ReadCorpusFile(Tool):
    name = "read_corpus_file"
    description = "按文件名读取本地研究语料的一个 Markdown 文件"
    parameters = {
        "type": "object",
        "properties": {"filename": {"type": "string", "description": "文件名，如 a.md"}},
        "required": ["filename"],
    }

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        filename = Path(str(arguments.get("filename", ""))).name  # 防目录穿越
        path = CORPUS_DIR / filename
        if not path.is_file():
            return ToolResult(ok=False, error=f"not found: {filename}")
        return ToolResult(output=path.read_text(encoding="utf-8")[:4000])


async def main() -> None:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        sys.exit("请先设置 OPENAI_API_KEY（离线演示请运行 02_loop_offline_demo.py）")

    registry = ToolRegistry()
    registry.register(ListCorpusFiles())
    registry.register(ReadCorpusFile())

    agent = Agent(
        name="research-analyst",
        provider=OpenAIProvider(api_key=api_key, model="gpt-4o"),
        config=Config(),
        registry=registry,
    )

    def show(event: Event) -> None:
        if event.type is EventType.THINKING:
            print(f"[{event.step}] 思考: {event.data['text'][:80]}")
        elif event.type is EventType.TOOL_CALL:
            print(
                f"[{event.step}] 调用: {event.data['tool_call']['name']} "
                f"{event.data['tool_call']['arguments']}"
            )
        elif event.type is EventType.TOOL_RESULT:
            evidence = event.data["evidence"]
            mark = "OK" if evidence["ok"] else f"FAIL({evidence['error']})"
            print(f"[{event.step}] 结果: {mark} {str(evidence['output'])[:60]}")
        elif event.type is EventType.DONE:
            print(f"[{event.step}] 完成: {event.data['result']}")
        elif event.type is EventType.ERROR:
            print(f"[{event.step}] 错误: {event.data}")

    async for event in agent.stream(
        "阅读本地语料目录，综合各文件内容，输出一份关于 Agent 可靠执行的三点要点总结"
    ):
        show(event)

    state = agent._last_state
    if state is not None:
        print(f"\n步数={state.step} usage={state.usage} status={state.status.value}")


if __name__ == "__main__":
    asyncio.run(main())
