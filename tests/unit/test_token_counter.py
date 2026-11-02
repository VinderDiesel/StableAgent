"""P3 A 节单测：token 估算的确定性、边界与注入面（验收 A2/A6，A3 的注入协议部分）。"""

from __future__ import annotations

from typing import Any

import pytest

from stableagent.context.tokens import (
    HeuristicTokenCounter,
    TokenCounter,
    count_context,
    resolve_token_counter,
)
from stableagent.core.state import Message, ToolCall

COUNTER = HeuristicTokenCounter()


def test_a6_count_is_deterministic_across_calls() -> None:
    text = "重复调用必须同值：预算阈值测试依赖这一点。" * 3
    assert COUNTER.count(text) == COUNTER.count(text)


def test_a6_empty_inputs_count_zero() -> None:
    assert COUNTER.count("") == 0
    assert COUNTER.count_messages([]) == 0
    assert COUNTER.count_tools([]) == 0


def test_a6_long_text_scales_linearly_with_char_count() -> None:
    short = COUNTER.count("a" * 100)
    long = COUNTER.count("a" * 400)
    assert long == pytest.approx(short * 4, abs=1)


def test_a6_wide_chars_cost_more_per_char_than_ascii() -> None:
    # 同字符数下，中文的 BPE 成本明显高于等长英文，估算需体现这个方向。
    assert COUNTER.count("长" * 20) > COUNTER.count("a" * 20)


def test_a6_mixed_script_counts_both_domains() -> None:
    mixed = COUNTER.count("checkpoint 恢复")
    assert mixed > COUNTER.count("checkpoint")
    assert mixed > COUNTER.count("恢复")


def test_a6_nested_schema_json_is_counted() -> None:
    flat = {"name": "t", "description": "d", "parameters": {"type": "object"}}
    nested = {
        "name": "t",
        "description": "d",
        "parameters": {
            "type": "object",
            "properties": {"a": {"type": "string", "enum": ["x", "y"]}},
            "required": ["a"],
        },
    }
    assert COUNTER.count_tools([nested]) > COUNTER.count_tools([flat])


def test_a2_tool_specs_are_part_of_the_context_cost() -> None:
    specs = [
        {"name": f"tool_{i}", "description": "does a thing", "parameters": {}} for i in range(10)
    ]
    assert COUNTER.count_tools(specs) > 0
    assert COUNTER.count_tools(specs + specs) == 2 * COUNTER.count_tools(specs)


def test_a2_key_order_does_not_change_the_count() -> None:
    a = {"name": "t", "parameters": {"type": "object", "title": "Z"}, "description": "d"}
    b = {"description": "d", "parameters": {"title": "Z", "type": "object"}, "name": "t"}
    assert COUNTER.count_tools([a]) == COUNTER.count_tools([b])


def test_messages_with_tool_calls_cost_more_than_empty_assistant() -> None:
    bare = Message(role="assistant", content="")
    calling = Message(
        role="assistant",
        content="",
        tool_calls=[ToolCall(id="c1", name="search", arguments={"q": "checkpoint design"})],
    )
    assert COUNTER.count_messages([calling]) > COUNTER.count_messages([bare])


def test_count_context_adds_messages_and_tools() -> None:
    messages = [Message(role="user", content="问题")]
    specs: list[dict[str, Any]] = [{"name": "search", "description": "d", "parameters": {}}]
    assert count_context(COUNTER, messages, specs) == (
        COUNTER.count_messages(messages) + COUNTER.count_tools(specs)
    )
    assert count_context(COUNTER, messages) == COUNTER.count_messages(messages)


class _OneTokenPerMessage:
    """桩计数器：证明"量"是可替换的判据，而非硬编码在策略里（A3 的协议面）。"""

    def count(self, text: str) -> int:
        return 1

    def count_messages(self, messages: list[Message]) -> int:
        return len(messages)

    def count_tools(self, specs: list[dict[str, Any]]) -> int:
        return len(specs)


def test_a3_custom_counter_satisfies_protocol_and_injects() -> None:
    counter: TokenCounter = _OneTokenPerMessage()
    resolved = resolve_token_counter(counter)
    assert resolved is counter
    messages = [Message(role="user", content="x") for _ in range(3)]
    assert count_context(resolved, messages, [{}, {}]) == 5


def test_a3_default_counter_used_when_none() -> None:
    assert isinstance(resolve_token_counter(None), HeuristicTokenCounter)


def test_a3_partial_injection_fails_loudly() -> None:
    class _Broken:
        def count(self, text: str) -> int:
            return 0

    with pytest.raises(TypeError, match="count_messages"):
        resolve_token_counter(_Broken())  # type: ignore[arg-type]


def test_a3_injection_missing_any_protocol_method_raises() -> None:
    class _OnlyCountAndMessages:
        def count(self, text: str) -> int:
            return 0

        def count_messages(self, messages: list) -> int:
            return 0

    with pytest.raises(TypeError, match="count_tools"):
        resolve_token_counter(_OnlyCountAndMessages())  # type: ignore[arg-type]


def test_a6_named_message_costs_more_than_unnamed() -> None:
    unnamed = Message(role="tool", content="结果", tool_call_id="c1")
    named = Message(role="tool", content="结果", name="search_docs", tool_call_id="c1")
    assert COUNTER.count_messages([named]) > COUNTER.count_messages([unnamed])
