"""Provider 抽象契约 v1.0.0：统一 sync / async / stream 三种调用形态。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel, Field

from stableagent.core.state import Message


class ToolCall(BaseModel):
    """模型发起的一次工具调用请求。"""

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ProviderResponse(BaseModel):
    """非流式调用的归一化返回。"""

    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: dict[str, int] = Field(default_factory=dict)


class ProviderChunk(BaseModel):
    """流式调用的增量片段；done=True 表示本次流终止。"""

    text: str = ""
    tool_call: ToolCall | None = None
    done: bool = False


class Provider(ABC):
    """模型提供方抽象。实现方必须同时提供三种形态，loop 只依赖这三者。"""

    @abstractmethod
    def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        **options: Any,
    ) -> ProviderResponse: ...

    @abstractmethod
    async def acomplete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        **options: Any,
    ) -> ProviderResponse: ...

    @abstractmethod
    def stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        **options: Any,
    ) -> AsyncIterator[ProviderChunk]: ...
