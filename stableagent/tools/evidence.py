"""P3 E1：`Evidence` 的家（Roadmap 交付物清单点名的 `tools/evidence.py`）。

原先住在 `registry.py` 里，与 P1 的 `ToolCall` 同形——后者在 P1 收口时移到 `core/state.py`
并在 `providers/base.py` 保留重导出，**旧导入路径不破**。这里照同一先例搬：
`from stableagent.tools.registry import Evidence` 仍然可用（重导出见 `registry.py`），
`stableagent/__init__.py` 改指本模块这个唯一事实源。

为什么值得搬：`Evidence` 的不变式与归一化规则从 E2 起要长东西，把它们塞进
"注册与调用入口"那个文件里会让两个契约同居一处，`00-contracts.md` 的索引也指不准。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, model_validator

from stableagent.providers.base import ToolCall


class Evidence(BaseModel):
    """工具调用的归一化可追溯记录——loop 与上下文只消费 Evidence。

    不变式：`ok == (error is None)`，即"失败当且仅当带错误说明"。
    E2 之前这条只是文档上写着、无人执行：工具返回 `ToolResult(ok=False)` 而不填 `error`
    是合法的，于是 `ok=False, error=None` 的 Evidence 流到 `core/loop.py:121` 的投影
    `str(e.output if e.ok else e.error)`，发给模型的那条 tool 消息就成了字面量 `"None"`
    ——失败原因在上下文里彻底消失，而压缩看到的东西与一条正常输出无从分辨。
    所以现在由模型自己归一化，不指望调用方自觉。
    """

    tool_call: ToolCall
    ok: bool
    output: Any = None
    error: str | None = None

    @model_validator(mode="after")
    def _enforce_invariant(self) -> Evidence:
        if self.error == "":
            # 空串不是"有说明的失败"：先按"没报错"处理，否则 output 会被下一条判掉
            self.error = None
        if not self.ok and self.error is None:
            # 失败但没给原因：补一句确定性说明，别把 "None" 当输出发给模型
            self.error = f"tool {self.tool_call.name} failed without an error message"
        elif self.ok and self.error is not None:
            # 同时给了 output 和 error：报错了就不算成功（反向选择会把失败静默成成功）
            self.ok = False
        return self
