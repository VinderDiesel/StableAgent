"""历史压缩三档（P3 B 节）：truncation / summary / hybrid。

契约边界（docs/p3-dod-checklist.md L1/L5）：
- 压缩只经 `ContextAssembler` 位点生效，`core/loop.py` 零修改；
- 压缩是 **快照的纯函数**：`compress(state.messages)` 的全部内容来自快照，
  进程内不留缓存——崩溃恢复后重跑同一份快照必得同一份上下文（L5 的最强形式：
  没有"重算还是不等价"的两难，因为压根没有第二份状态）。
- 折叠前缀的载体是一条 `role="system"` + `name=SUMMARY_NAME` 的消息：
  输入里已有该标记即视为"已折叠部分的缓存"，输出至多一条，绝不叠加。

装配顺序（B3 锁死，换了质量保留率就变）：
先折工具结果 → 再摘要更早的历史 → 最后才截断。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from stableagent.core.state import ContextConfig, Message

SUMMARY_NAME = "context_summary"
"""折叠前缀在上下文里的固定身份，也是它进快照的唯一载体（D1 的 `ContextEngine.fold()`）。"""


@runtime_checkable
class Summariser(Protocol):
    """摘要生成器协议。默认实现离线确定性；接真实模型经 `summariser=` 注入。"""

    def summarise(self, messages: list[Message]) -> str: ...


class ExtractiveSummariser:
    """离线抽取式摘要：定长摘录 + 定序，零模型调用、可复现。"""

    def __init__(self, per_message_chars: int = 60, max_messages: int = 40) -> None:
        self._chars = per_message_chars
        self._max_messages = max_messages

    def summarise(self, messages: list[Message]) -> str:
        lines: list[str] = []
        for message in messages[: self._max_messages]:
            label = message.name or message.role
            if message.tool_calls:
                names = ", ".join(call.name for call in message.tool_calls)
                lines.append(f"- {label} 调用工具: {names}")
            text = " ".join(message.content.split())
            if text:
                lines.append(f"- {label}: {text[: self._chars]}")
        return "\n".join(lines)


class CompressionResult(BaseModel):
    """压缩产出：`kept` 是发给 Provider 的消息，`levels` 记录实际走了哪几档（D4 可观测）。"""

    kept: list[Message]
    summary: str = ""
    folded: int = 0
    levels: tuple[str, ...] = ()


def is_summary_message(message: Message) -> bool:
    return message.role == "system" and message.name == SUMMARY_NAME


def split_summary(messages: list[Message]) -> tuple[list[Message], str]:
    """取出折叠前缀缓存：返回（去掉该标记后的消息，缓存文本）。至多一条，多余的合并。"""
    cached: list[str] = []
    rest: list[Message] = []
    for message in messages:
        if is_summary_message(message):
            cached.append(message.content)
        else:
            rest.append(message)
    return rest, "\n".join(text for text in cached if text)


def partition(messages: list[Message], keep_recent: int) -> tuple[list[Message], list[Message]]:
    """按"永不拆散 tool_call/tool_result 配对"（B1）切出 (折叠段, 保留段)。

    不变式：system 消息与首条 user 消息（任务本体）常驻；
    保留段的第一条不得是 `tool` 消息——那意味着它的 `assistant(tool_calls)` 被留在了折叠段。
    """
    pinned = [m for m in messages if m.role == "system"]
    body = [m for m in messages if m.role != "system"]
    if body and body[0].role == "user":
        pinned.append(body.pop(0))
    if keep_recent < 0:
        raise ValueError(f"keep_recent_messages 不能为负：{keep_recent}")
    if len(body) <= keep_recent:
        return [], pinned + body
    cut = len(body) - keep_recent
    while 0 < cut < len(body) and body[cut].role == "tool":  # 边界回退，配对留在保留段
        cut -= 1
    folded = body[:cut]
    return folded, pinned + body[cut:]


def _summary_message(text: str) -> Message:
    return Message(role="system", name=SUMMARY_NAME, content=text)


def _clamp(text: str, limit: int) -> str:
    """超长时保留尾部（最近的折叠信息价值最高），定长截断保证确定性。"""
    if len(text) <= limit:
        return text
    return text[-limit:]


def _merge(cached: str, fresh: str, limit: int) -> str:
    if not fresh:
        return cached
    if not cached:
        return _clamp(fresh, limit)
    return _clamp(f"{cached}\n{fresh}", limit)


class ContextCompressor(ABC):
    """压缩策略抽象：纯函数，输入输出都只由快照决定。"""

    def __init__(self, config: ContextConfig, summariser: Summariser | None = None) -> None:
        self._config = config
        self._summariser = summariser or ExtractiveSummariser()

    @abstractmethod
    def compress(self, messages: list[Message]) -> CompressionResult: ...

    def _fold(self, messages: list[Message]) -> tuple[list[Message], list[Message], str]:
        rest, cached = split_summary(messages)
        folded, kept = partition(rest, self._config.keep_recent_messages)
        return folded, kept, cached


class TruncationCompressor(ContextCompressor):
    """B1：只丢最旧的，不生成摘要。信息会真丢——这是它的取舍，不是缺陷。"""

    def compress(self, messages: list[Message]) -> CompressionResult:
        folded, kept, cached = self._fold(messages)
        out = [_summary_message(cached), *kept] if cached else kept
        return CompressionResult(
            kept=out, summary=cached, folded=len(folded), levels=("truncation",) if folded else ()
        )


class SummaryCompressor(ContextCompressor):
    """B2：折叠段经摘要器压成一条 system 消息，常驻上下文替代原始旧消息段。"""

    def compress(self, messages: list[Message]) -> CompressionResult:
        folded, kept, cached = self._fold(messages)
        fresh = self._summariser.summarise(folded) if folded else ""
        summary = _merge(cached, fresh, self._config.max_summary_chars)
        out = [_summary_message(summary), *kept] if summary else kept
        return CompressionResult(
            kept=out,
            summary=summary,
            folded=len(folded),
            levels=("summary",) if folded else (),
        )


class HybridCompressor(ContextCompressor):
    """B3：折工具结果 → 摘要更早历史 → 截断，顺序由本方法体与单测双重锁死。"""

    def compress(self, messages: list[Message]) -> CompressionResult:
        folded, kept, cached = self._fold(messages)
        levels: list[str] = []
        excerpted = [m for m in folded if m.role == "tool"]

        if excerpted:
            # 第一档：工具结果是 token 大头，先就地摘录（保留段原文不动）
            limit = self._config.tool_result_excerpt_chars
            folded = [
                m.model_copy(update={"content": " ".join(m.content.split())[:limit]})
                if m.role == "tool"
                else m
                for m in folded
            ]
            levels.append("tool_excerpt")

        fresh = self._summariser.summarise(folded) if folded else ""
        if fresh:
            levels.append("summary")
        summary = _merge(cached, fresh, self._config.max_summary_chars)

        out = [_summary_message(summary), *kept] if summary else kept
        if folded:
            levels.append("truncation")
        return CompressionResult(
            kept=out, summary=summary, folded=len(folded), levels=tuple(levels)
        )


COMPRESSIONS: dict[str, type[ContextCompressor]] = {
    "truncation": TruncationCompressor,
    "summary": SummaryCompressor,
    "hybrid": HybridCompressor,
}


def resolve_compression(
    config: ContextConfig, summariser: Summariser | None = None
) -> ContextCompressor | None:
    """`"none"`（默认）返回 None：不压缩，行为与 P2 逐条一致（L4）。"""
    if config.compression == "none":
        return None
    try:
        strategy = COMPRESSIONS[config.compression]
    except KeyError:
        raise KeyError(
            f"unknown compression {config.compression!r}; registered: {sorted(COMPRESSIONS)}"
        ) from None
    return strategy(config, summariser)
