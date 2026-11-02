"""动态能力视图（P3 C 节）：每步只把"这次可能用得上"的工具规格发给模型。

接入点是 loop 预留的 `tool_selector(specs, state)`（`core/loop.py:61`），零修改 loop。
三条硬规则，按重要性排：
- **召回护栏（C3）**：本次运行里模型已经调过的工具名进常驻集合，筛选结果必须 ⊇ 常驻集合。
  裁掉一个正在用的工具 = 模型下一步想用就找不到，比多花 token 贵得多。
- **确定性排序（C5）**：输出按名称字典序，不依赖 dict 插入序或哈希——否则同一任务两次
  跑出来的 prompt 不一样，F 节的 benchmark 没法复现。
- **兜底（C4）**：筛到空集就退回全量并记 `fallback`。空工具列表等于当场废掉 Agent。
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from stableagent.context.tokens import TokenCounter, count_context, resolve_token_counter
from stableagent.core.state import AgentState, ContextConfig, Message

ToolSelector = Callable[[list[dict[str, Any]], AgentState], list[dict[str, Any]]]
"""与 `core/loop.py` 的 `ToolSelector` 同形（不导入它：loop 已经依赖 context，反向导入成环）。"""

__all__ = [
    "CAPABILITY_FILTERS",
    "CapabilityFilter",
    "CapabilityReport",
    "RelevanceFilter",
    "StaticFilter",
    "as_tool_selector",
    "query_terms",
    "resident_tool_names",
    "resolve_capability_filter",
]

# 词面匹配的最小分词：拉丁词跑 + CJK 单字。够用在"工具名/描述里有没有任务词"这一层，
# 不假装是分词器——分词器会是第 4 个运行时依赖（L3 不允许）。
_TOKEN_PATTERN = re.compile(r"[a-z0-9_]+|[㐀-䶿一-鿿]")

# 虚词与单字母不算任务词：否则 "a"/"the" 会把每个英文工具描述都命中，
# 相关性筛选退化成"全量发出"，还看不出来。
_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "into",
        "onto",
        "are",
        "was",
        "were",
        "has",
        "have",
        "had",
        "not",
        "but",
        "you",
        "your",
        "our",
        "its",
        "them",
        "they",
        "then",
        "than",
        "too",
        "also",
        "just",
    }
)
_MIN_TERM_LENGTH = 3
# 拉丁词要 3 个字母起：两字母的词（on/an/in/to）做子串匹配会命中 "content"、"analytics"
# 这类无关描述，把相关性退化成噪声。CJK 单字不受此限（单字即语素）。


class CapabilityReport(BaseModel):
    """一次筛选的可断言产出：省了多少、留了谁、有没有走兜底（D 节 ContextReport 的原料）。"""

    kept: int
    dropped: int
    dropped_tokens: int = 0
    fallback: bool = False
    resident: tuple[str, ...] = ()


def resident_tool_names(messages: list[Message]) -> set[str]:
    """本次运行实际用过的工具名：assistant 的 tool_calls 与 tool 消息的 name 都算。"""
    names: set[str] = set()
    for message in messages:
        for call in message.tool_calls:
            names.add(call.name)
        if message.role == "tool" and message.name:
            names.add(message.name)
    return names


def _is_significant(token: str) -> bool:
    """CJK 单字本身就是语素，保留；拉丁词要够长且不是虚词。"""
    if "\u3400" <= token <= "\u4dbf" or "\u4e00" <= token <= "\u9fff":
        return True
    return len(token) >= _MIN_TERM_LENGTH and token not in _STOPWORDS


def query_terms(state: AgentState) -> list[str]:
    """任务词面 = user 与 assistant 文本里的实词，定序去重（确定性，不用哈希序）。"""
    terms: set[str] = set()
    for message in state.messages:
        if message.role in ("user", "assistant"):
            terms.update(
                token
                for token in _TOKEN_PATTERN.findall(message.content.lower())
                if _is_significant(token)
            )
    return sorted(terms)


def _haystack(spec: dict[str, Any]) -> str:
    return f"{spec.get('name', '')} {spec.get('description', '')}".lower()


class CapabilityFilter(ABC):
    """能力视图抽象：`(specs, state) -> specs`，与 loop 的 `ToolSelector` 同形。"""

    def __init__(self, config: ContextConfig, counter: TokenCounter | None = None) -> None:
        self._config = config
        self._counter = resolve_token_counter(counter)
        self.last_report = CapabilityReport(kept=0, dropped=0)

    @abstractmethod
    def select(self, specs: list[dict[str, Any]], state: AgentState) -> list[dict[str, Any]]: ...

    def _report(
        self,
        specs: list[dict[str, Any]],
        kept: list[dict[str, Any]],
        resident: set[str],
        *,
        fallback: bool = False,
    ) -> list[dict[str, Any]]:
        dropped = [spec for spec in specs if spec not in kept]
        ordered = sorted(kept, key=lambda spec: str(spec.get("name", "")))  # C5
        self.last_report = CapabilityReport(
            kept=len(ordered),
            dropped=len(dropped),
            dropped_tokens=count_context(self._counter, [], dropped),  # C6
            fallback=fallback,
            resident=tuple(sorted(resident)),
        )
        return ordered


class StaticFilter(CapabilityFilter):
    """默认（`capability_filter="static"`）：全量、原序发出，与 P2 逐条一致（L4）。

    连排序都不做——注册顺序也是既有行为的一部分，动它就等于默认路径回归。
    """

    def select(self, specs: list[dict[str, Any]], state: AgentState) -> list[dict[str, Any]]:
        self.last_report = CapabilityReport(
            kept=len(specs),
            dropped=0,
            resident=tuple(sorted(resident_tool_names(state.messages))),
        )
        return list(specs)


class RelevanceFilter(CapabilityFilter):
    """任务相关性：按词面命中排序，取前 `max_visible_tools`，常驻集合无条件保留。"""

    def select(self, specs: list[dict[str, Any]], state: AgentState) -> list[dict[str, Any]]:
        resident = resident_tool_names(state.messages)
        terms = query_terms(state)
        by_name = {str(spec.get("name", "")): spec for spec in specs}
        scored = sorted(
            (
                (sum(1 for term in terms if term in _haystack(spec)), index, spec)
                for index, spec in enumerate(specs)
            ),
            key=lambda item: (-item[0], item[1]),
        )
        selected: dict[str, dict[str, Any]] = {
            name: by_name[name] for name in sorted(resident) if name in by_name
        }
        for score, _, spec in scored:
            if len(selected) >= self._config.max_visible_tools:
                break
            if score > 0:
                name = str(spec.get("name", ""))
                selected[name] = by_name[name]
        if not selected:  # C4：一个都没命中就退回全量，绝不交出空能力清单
            return self._report(specs, list(specs), resident, fallback=True)
        return self._report(specs, list(selected.values()), resident)


CAPABILITY_FILTERS: dict[str, type[CapabilityFilter]] = {
    "static": StaticFilter,
    "relevance": RelevanceFilter,
}


def resolve_capability_filter(
    config: ContextConfig, counter: TokenCounter | None = None
) -> CapabilityFilter:
    try:
        strategy = CAPABILITY_FILTERS[config.capability_filter]
    except KeyError:
        raise KeyError(
            f"unknown capability_filter {config.capability_filter!r}; "
            f"registered: {sorted(CAPABILITY_FILTERS)}"
        ) from None
    return strategy(config, counter)


def as_tool_selector(capability_filter: CapabilityFilter) -> ToolSelector:
    """把筛选器变成 loop 认识的 `tool_selector` 形状（C2：接入位点，不改 loop）。"""

    def selector(specs: list[dict[str, Any]], state: AgentState) -> list[dict[str, Any]]:
        return capability_filter.select(specs, state)

    return selector
