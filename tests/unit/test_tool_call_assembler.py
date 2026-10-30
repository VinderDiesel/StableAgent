"""A4：流式 function calling 的 chunk 重组（ToolCallAssembler 单测）。"""

import pytest

from stableagent.core.state import ToolCall
from stableagent.providers.openai import ToolCallAssembler


def test_arguments_split_across_chunks_are_reassembled() -> None:
    assembler = ToolCallAssembler()
    assembler.feed(
        [
            {
                "index": 0,
                "id": "call_1",
                "function": {"name": "echo", "arguments": '{"te'},
            }
        ]
    )
    assembler.feed([{"index": 0, "function": {"arguments": 'xt": "hi"}'}}])
    assert assembler.calls() == [ToolCall(id="call_1", name="echo", arguments={"text": "hi"})]


def test_parallel_tool_calls_ordered_by_index() -> None:
    assembler = ToolCallAssembler()
    assembler.feed(
        [
            {"index": 1, "id": "b", "function": {"name": "second", "arguments": "{}"}},
            {"index": 0, "id": "a", "function": {"name": "first", "arguments": "{}"}},
        ]
    )
    calls = assembler.calls()
    assert [c.id for c in calls] == ["a", "b"]
    assert [c.name for c in calls] == ["first", "second"]


def test_incomplete_json_arguments_raise_valueerror() -> None:
    assembler = ToolCallAssembler()
    assembler.feed([{"index": 0, "id": "x", "function": {"name": "n", "arguments": '{"a"'}}])
    with pytest.raises(ValueError, match="invalid tool_call arguments"):
        assembler.calls()


def test_no_tool_calls_yields_empty_list() -> None:
    assert ToolCallAssembler().calls() == []
