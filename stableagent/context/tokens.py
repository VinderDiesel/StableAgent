"""Token 估算（P3 A 节）：可注入协议 + 启发式默认实现，**不引入新依赖**。

估算而非精确：BPE 计数需要词表（tiktoken 一类的重依赖），与 L3"运行时依赖仍为 3 个"冲突。
所以本模块的量是**决策用的相对量**，真实 token 数由 Provider 回报的 `usage` 事后校准（A4），
两者的偏差必须进 benchmark 报告（A5）——省一个"没人校准过的数字"优化的坑。

启发式的两条取向：
- 中日韩与全角字符 ≈ 1 token/字（BPE 在 CJK 上基本退化到单字）；
- 其余字符 ≈ 4 字/token（英文的常见量级）。
纯函数、无随机、`sort_keys` 定序：同一输入永远同一输出，否则预算阈值测试不可复现。
"""

from __future__ import annotations

import json
import math
from typing import Any, Protocol, runtime_checkable

from stableagent.core.state import Message

__all__ = [
    "HeuristicTokenCounter",
    "TokenCounter",
    "count_context",
    "resolve_token_counter",
]

# 需要按"约 1 token/字"计价的水域：CJK 汉字、日文字符、Hangul、CJK 标点与全角形式。
_WIDE_RANGES: tuple[tuple[int, int], ...] = (
    (0x1100, 0x11FF),  # Hangul Jamo
    (0x2E80, 0x303F),  # CJK 部首、标点
    (0x3040, 0x30FF),  # Hiragana / Katakana
    (0x3130, 0x318F),  # Hangul Compatibility Jamo
    (0x31F0, 0x9FFF),  # CJK 扩展与主区
    (0xA960, 0xD7FF),  # Hangul Syllables
    (0xF900, 0xFAFF),  # CJK 兼容汉字
    (0xFE30, 0xFE4F),  # CJK 兼容形式
    (0xFF00, 0xFF60),  # 全角形式
    (0xFFE0, 0xFFE6),  # 全角符号
    (0x20000, 0x2FA1F),  # CJK 扩展 B–F
)

MESSAGE_OVERHEAD = 4  # 每条消息的角色/分隔开销（与主流 chat 模板同量级）
TOOL_SPEC_OVERHEAD = 8  # 每个工具规格的结构开销


def _is_wide(char: str) -> bool:
    code = ord(char)
    return any(low <= code <= high for low, high in _WIDE_RANGES)


@runtime_checkable
class TokenCounter(Protocol):
    """预算治理只依赖这三个方法；自定义计数器的注入面（A3）。"""

    def count(self, text: str) -> int: ...

    def count_messages(self, messages: list[Message]) -> int: ...

    def count_tools(self, specs: list[dict[str, Any]]) -> int: ...


class HeuristicTokenCounter:
    """默认实现：字符域计数 + 固定结构开销。"""

    def __init__(
        self,
        *,
        wide_tokens_per_char: float = 1.0,
        ascii_tokens_per_char: float = 0.25,
        message_overhead: int = MESSAGE_OVERHEAD,
        tool_spec_overhead: int = TOOL_SPEC_OVERHEAD,
    ) -> None:
        self._wide = wide_tokens_per_char
        self._ascii = ascii_tokens_per_char
        self._message_overhead = message_overhead
        self._tool_overhead = tool_spec_overhead

    def count(self, text: str) -> int:
        if not text:
            return 0
        wide = sum(1 for char in text if _is_wide(char))
        plain = len(text) - wide
        return math.ceil(wide * self._wide + plain * self._ascii)

    def count_messages(self, messages: list[Message]) -> int:
        total = 0
        for message in messages:
            total += self._message_overhead + self.count(message.content)
            if message.name:
                total += self.count(message.name)
            if message.tool_calls:
                total += self.count(_dump(message.model_dump(mode="json")["tool_calls"]))
        return total

    def count_tools(self, specs: list[dict[str, Any]]) -> int:
        """工具规格也是发出去的内容（A2）：能力视图的收益必须算这一块。"""
        total = 0
        for spec in specs:
            total += self._tool_overhead + self.count(_dump(spec))
        return total


def _dump(value: Any) -> str:
    # sort_keys：顺序不同不得改变计数，否则"裁工具"的省量里混进了哈希噪声。
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def count_context(
    counter: TokenCounter,
    messages: list[Message],
    specs: list[dict[str, Any]] | None = None,
) -> int:
    """将要发出的总量（预算判据 D2 的比较对象）：组装后的消息 + 工具规格。"""
    return counter.count_messages(messages) + counter.count_tools(specs or [])


def resolve_token_counter(counter: TokenCounter | None) -> TokenCounter:
    """注入点：缺省启发式，其他实现必须满足协议（缺方法即早失败，不静默降级）。"""
    if counter is None:
        return HeuristicTokenCounter()
    for name in ("count", "count_messages", "count_tools"):
        if not callable(getattr(counter, name, None)):
            raise TypeError(f"token_counter 缺少方法 {name!r}")
    return counter
