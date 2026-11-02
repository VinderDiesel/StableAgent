"""预算治理的数据面（P3 D 节）：估算结果、决策、每步报告。

判据只有一种语言：**"本次将要发出去的东西"**。`ContextReport.before` 是"不组装不裁剪"
的全量估算，`after` 是实际发出的估算，二者之差才是策略真省下的量——
拿"历史全长"当分母的节省率是好看的数字，不是可复现的数字（F3 的教训）。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from stableagent.core.state import Message

__all__ = ["BudgetAction", "BudgetDecision", "ContextReport", "count_orphan_tool_calls"]

BudgetAction = Literal["none", "compress", "degrade", "reject"]


def count_orphan_tool_calls(messages: list[Message]) -> int:
    """E4 自检：带 `tool_calls` 的 assistant 之后是否紧跟其全部 `tool` 结果。

    正常上下文应为 0。非 0 意味着某处裁剪把配对切断了——Provider 会直接报错，
    所以每次组装都算一遍并写进 `ContextReport.orphans`，让"没拆散"是可断言的事实
    而不是注释里的承诺。
    """
    orphans = 0
    for index, message in enumerate(messages):
        if not message.tool_calls:
            continue
        following = messages[index + 1 : index + 1 + len(message.tool_calls)]
        matched = {m.tool_call_id for m in following if m.role == "tool"}
        orphans += sum(1 for call in message.tool_calls if call.id not in matched)
    return orphans


class ContextReport(BaseModel):
    """一步的上下文决策全记录（D4/D6）：动了哪几档、前后各多少、有没有压到地板。"""

    step: int = 0
    action: BudgetAction = "none"
    limit: int | None = None
    before: int = 0
    after: int = 0
    levels: tuple[str, ...] = ()
    levels_tried: int = 0
    tools_total: int = 0
    tools_kept: int = 0
    tools_dropped_tokens: int = 0
    orphans: int = 0
    within_budget: bool = True
    exhausted: bool = False
    estimated_prompt_tokens: int = 0
    observed_prompt_tokens: int | None = None
    """A4 事后校准：本步 Provider 回报的 `usage.prompt_tokens` **增量**。

    `state.usage` 是跨步累加的，直接抄过来会和"本步将发出的量"口径不一致，
    两个数就没法相减了。基线在 `preflight()` 重置，所以 resume/recover 后
    第一份报告的增量仍然只算本次这段。
    """


class BudgetDecision(BaseModel):
    """外层 preflight 的结论：要不要拦、拟走哪条阶梯、压到地板了没有。"""

    within_budget: bool = True
    estimate: int = 0
    limit: int | None = None
    action: BudgetAction = "none"
    exhausted: bool = False
    levels_tried: int = 0
    detail: str = ""
