"""上下文组装位点（P1 预留，P3 主场）：loop 经 assembler 取得发给 Provider 的消息。

契约：`core/loop.py` 不得内联拼装 history；P3 的压缩/截断/混合策略以
注册新 assembly 名称的方式接入，零修改 loop。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from stableagent.core.state import ContextConfig, Message


class ContextAssembler(ABC):
    @abstractmethod
    def assemble(self, messages: list[Message]) -> list[Message]: ...


class PassthroughAssembler(ContextAssembler):
    """默认策略：全量历史原样发出（ContextConfig.assembly == 'passthrough'）。"""

    def assemble(self, messages: list[Message]) -> list[Message]:
        return list(messages)


ASSEMBLERS: dict[str, type[ContextAssembler]] = {
    "passthrough": PassthroughAssembler,
}


def resolve_assembler(config: ContextConfig) -> ContextAssembler:
    try:
        return ASSEMBLERS[config.assembly]()
    except KeyError:
        raise KeyError(
            f"unknown assembly {config.assembly!r}; registered: {sorted(ASSEMBLERS)}"
        ) from None
