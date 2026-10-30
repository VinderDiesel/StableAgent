"""Tool 抽象契约 v1.0.0：统一入参/出参 + 失败归一化 → Evidence。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from pydantic import BaseModel

from stableagent.providers.base import ToolCall


class ToolResult(BaseModel):
    """工具实现的直接返回。"""

    ok: bool = True
    output: Any = None
    error: str | None = None


class Evidence(BaseModel):
    """工具调用的归一化可追溯记录——loop 与上下文只消费 Evidence。

    不变式：ok=False 当且仅当 error 非空（含异常被归一化的场景）。
    """

    tool_call: ToolCall
    ok: bool
    output: Any = None
    error: str | None = None


class Tool(ABC):
    """工具抽象。execute 不做异常吞噬，失败由 ToolRegistry.call 统一归一化。"""

    name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    parameters: ClassVar[dict[str, Any]] = {"type": "object", "properties": {}}

    @abstractmethod
    async def execute(self, arguments: dict[str, Any]) -> ToolResult: ...


class ToolRegistry:
    """工具注册与调用入口，负责失败归一化。"""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if not tool.name:
            raise ValueError("Tool.name must be a non-empty string")
        if tool.name in self._tools:
            raise ValueError(f"duplicated tool name: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError:
            raise KeyError(f"tool not registered: {name}") from None

    def specs(self) -> list[dict[str, Any]]:
        """供 Provider / 动态能力视图消费的工具规格列表。"""
        return [
            {"name": t.name, "description": t.description, "parameters": t.parameters}
            for t in self._tools.values()
        ]

    async def call(self, tool_call: ToolCall) -> Evidence:
        try:
            tool = self.get(tool_call.name)
            result = await tool.execute(tool_call.arguments)
        except Exception as exc:
            return Evidence(
                tool_call=tool_call,
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
            )
        return Evidence(
            tool_call=tool_call,
            ok=result.ok,
            output=result.output,
            error=result.error,
        )
