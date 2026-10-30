"""D 节：P3 预留验收——组装位点 / 能力视图位点 / usage 穿透，全部零修改 loop。"""

from typing import Any

import pytest

from stableagent.context.assembly import (
    ASSEMBLERS,
    ContextAssembler,
    PassthroughAssembler,
    resolve_assembler,
)
from stableagent.core.state import AgentState, ContextConfig, Message
from tests.support import FakeProvider, final_response, tool_response
from tests.unit.test_loop_branches import collect, make_agent


class Last2Assembler(ContextAssembler):
    def assemble(self, messages: list[Message]) -> list[Message]:
        return messages[-2:]


def test_d1_resolve_default_passthrough() -> None:
    assert isinstance(resolve_assembler(ContextConfig()), PassthroughAssembler)


def test_d1_unknown_assembly_name_fails_explicitly() -> None:
    with pytest.raises(KeyError, match="unknown assembly"):
        resolve_assembler(ContextConfig(assembly="nope"))


def test_d1_new_strategy_registers_without_touching_loop() -> None:
    ASSEMBLERS["last2"] = Last2Assembler
    try:
        assert isinstance(resolve_assembler(ContextConfig(assembly="last2")), Last2Assembler)
    finally:
        del ASSEMBLERS["last2"]


async def test_d2_custom_assembler_truncates_provider_input() -> None:
    provider = FakeProvider(
        [tool_response("c1", "a"), tool_response("c2", "b"), final_response("end")]
    )
    agent = make_agent(provider, assembler=Last2Assembler())
    await collect(agent)
    # 第二次模型调用只收到最近 2 条消息，而 passthrough 会收到全部
    second_messages = provider.calls[1][0]
    assert len(second_messages) == 2
    assert [m.role for m in second_messages] == ["assistant", "tool"]


async def test_d3_tool_selector_narrows_specs_per_round() -> None:
    provider = FakeProvider([tool_response("c1", "a"), final_response("end")])

    def drop_after_first(specs: list[dict[str, Any]], state: AgentState) -> list[dict[str, Any]]:
        return [] if state.step >= 1 else specs

    agent = make_agent(provider, tool_selector=drop_after_first)
    await collect(agent)
    assert provider.calls[0][1] and provider.calls[0][1][0]["name"] == "echo"
    assert provider.calls[1][1] == []  # 第二轮能力视图已裁剪


async def test_d4_usage_accumulates_across_steps() -> None:
    provider = FakeProvider(
        [
            tool_response("c1", "a", {"prompt_tokens": 10, "completion_tokens": 2}),
            final_response("end", {"prompt_tokens": 20, "completion_tokens": 4}),
        ]
    )
    agent = make_agent(provider)
    await collect(agent)
    assert agent._last_state is not None
    assert agent._last_state.usage == {"prompt_tokens": 30, "completion_tokens": 6}
