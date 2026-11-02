"""上下文组装位点（P1 预留，P3 主场）：loop 经 assembler 取得发给 Provider 的消息。

契约：`core/loop.py` 不得内联拼装 history；P3 的压缩/截断/混合策略以
注册新 assembly 名称的方式接入，零修改 loop。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from stableagent.context.compression import (
    ContextCompressor,
    Summariser,
    resolve_compression,
)
from stableagent.core.state import ContextConfig, Message


class ContextAssembler(ABC):
    @abstractmethod
    def assemble(self, messages: list[Message]) -> list[Message]: ...


class PassthroughAssembler(ContextAssembler):
    """默认策略：全量历史原样发出（ContextConfig.assembly == 'passthrough'）。"""

    def assemble(self, messages: list[Message]) -> list[Message]:
        return list(messages)


class CompressedAssembler(ContextAssembler):
    """B 节接入点：先走 base 组装，再套一层压缩（`ContextConfig.compression`）。

    单一开关：`compression` 生效与否不依赖 `assembly` 取什么值，避免"配了压缩但
    assembly 还是 passthrough 于是静默失效"这种坑。压缩是 `messages` 的纯函数，
    因此重复调用与崩溃恢复后的重放都得到同一份上下文（L5/B6）。
    """

    def __init__(self, base: ContextAssembler, compressor: ContextCompressor) -> None:
        self._base = base
        self._compressor = compressor

    def assemble(self, messages: list[Message]) -> list[Message]:
        return list(self._compressor.compress(self._base.assemble(messages)).kept)


ASSEMBLERS: dict[str, type[ContextAssembler]] = {
    "passthrough": PassthroughAssembler,
}


def resolve_assembler(
    config: ContextConfig, summariser: Summariser | None = None
) -> ContextAssembler:
    try:
        base = ASSEMBLERS[config.assembly]()
    except KeyError:
        raise KeyError(
            f"unknown assembly {config.assembly!r}; registered: {sorted(ASSEMBLERS)}"
        ) from None
    compressor = resolve_compression(config, summariser)
    return base if compressor is None else CompressedAssembler(base, compressor)
