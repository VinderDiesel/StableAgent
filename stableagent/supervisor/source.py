"""Source 契约：把只读轮询结果标准化为 Trigger / Thread / Channel。

方向锁：本模块只 import `supervisor.model`；具体 Source 实现在 ``sources/``。
Channel 只是回写目的地（ADR-06 决策 8），不携带授权。
"""

from __future__ import annotations

import hashlib
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from stableagent.supervisor.model import Channel, ChannelKind, Thread, Trigger


class SourceError(Exception):
    """Source 层稳定错误：四分类决定 cursor health 与是否可重试。"""

    code = "source.error"

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.detail = message
        self.code = code


class SourceTransientError(SourceError):
    """网络抖动、限流、5xx：cursor 记 degraded，下一轮可重试。"""

    def __init__(self, message: str) -> None:
        super().__init__("source.transient", message)


class SourceAuthError(SourceError):
    """403：读权限不足或 token 失效，需要人工处理。"""

    def __init__(self, message: str) -> None:
        super().__init__("source.auth", message)


class SourceGoneError(SourceError):
    """404：仓库不可见或事件已不存在，继续轮询不会恢复。"""

    def __init__(self, message: str) -> None:
        super().__init__("source.gone", message)


class SourceSchemaError(SourceError):
    """载荷形状不合约：整批不落库，避免写进半个事实。"""

    def __init__(self, message: str) -> None:
        super().__init__("source.schema", message)


class SourceEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    trigger: Trigger
    thread: Thread
    channel: Channel | None = None


class PollBatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    events: list[SourceEvent] = Field(default_factory=list)
    next_cursor: str | None = None
    duplicates: int = Field(default=0, ge=0)


@runtime_checkable
class Source(Protocol):
    async def poll(self, *, limit: int = 100) -> PollBatch: ...


@runtime_checkable
class ChannelWriter(Protocol):
    async def write(self, *, channel: Channel, body: str) -> dict[str, Any]: ...


class FakeChannelWriter:
    """记录 `(channel, body)`；`fail_next` 抛一次后清空。"""

    def __init__(self) -> None:
        self.calls: list[tuple[Channel, str]] = []
        self.fail_next: Exception | None = None

    async def write(self, *, channel: Channel, body: str) -> dict[str, Any]:
        self.calls.append((channel, body))
        failure, self.fail_next = self.fail_next, None
        if failure is not None:
            raise failure
        return {"id": f"fake-{len(self.calls)}"}


def stable_trigger_id(external_id: str) -> str:
    return "tg-" + hashlib.sha256(external_id.encode("utf-8")).hexdigest()[:12]


def stable_channel_id(thread_key: str, kind: ChannelKind, external_ref: str) -> str:
    seed = f"{thread_key}|{kind.value}|{external_ref}"
    return "ch-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]


def issue_thread_key(repository: str, number: int) -> str:
    return f"issue:{repository}#{number}"


def pull_request_thread_key(repository: str, number: int) -> str:
    return f"pr:{repository}#{number}"


def review_thread_key(repository: str, pull_number: int, review_id: int) -> str:
    return f"review:{repository}#{pull_number}:{review_id}"


def repository_thread_key(repository: str) -> str:
    return f"repo:{repository}"


__all__ = [
    "ChannelWriter",
    "FakeChannelWriter",
    "PollBatch",
    "Source",
    "SourceAuthError",
    "SourceError",
    "SourceEvent",
    "SourceGoneError",
    "SourceSchemaError",
    "SourceTransientError",
    "issue_thread_key",
    "pull_request_thread_key",
    "repository_thread_key",
    "review_thread_key",
    "stable_channel_id",
    "stable_trigger_id",
]


def __getattr__(name: str) -> object:
    """延迟导出 GitHubSource，避免 source ↔ sources.github 循环导入。"""
    if name == "GitHubSource":
        from stableagent.supervisor.sources.github import GitHubSource

        return GitHubSource
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
