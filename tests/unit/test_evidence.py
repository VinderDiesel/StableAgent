"""E1 / E2：`Evidence` 的家与它的不变式。

E2 的"property 测试"在这里 = 对 `(ok, output, error)` 全组合扫一遍，断言
`ok == (error is None)` 恒成立——单点断言挡不住"另一种 sloppy 写法"。
"""

from typing import Any, ClassVar

import pytest

import stableagent
from stableagent.providers.base import Provider, ProviderResponse, ToolCall
from stableagent.tools.evidence import Evidence as _Canonical
from stableagent.tools.registry import Evidence as _ViaRegistry
from stableagent.tools.registry import Tool, ToolRegistry, ToolResult
from tests.support import FakeProvider, final_response

CALL = ToolCall(id="c-1", name="echo", arguments={"text": "ping"})


class _BaseEcho(Tool):
    name = "echo"
    description = "echo back the input"
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
    }
    result: ClassVar[ToolResult] = ToolResult()

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        return type(self).result


def echo_returning(result: ToolResult) -> type[Tool]:
    """造一个"就返回给定 ToolResult"的工具类。

    用子类而不是改共享类属性：参数化测试若都往同一个类上写 `result`，
    前一档的返回值会漏进后一档，等于把测试顺序写进了断言里。
    """
    return type("Echo", (_BaseEcho,), {"result": result})


def registry_for(result: ToolResult) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(echo_returning(result)())
    return registry


@pytest.mark.parametrize(
    "ok,output,error",
    [
        (True, "out", None),
        (False, None, "boom"),
        (False, None, None),  # 失败没给原因
        (False, "半个结果", None),  # 失败给了 output 没给原因
        (True, "out", ""),  # 空串不算"有说明的失败"
        (True, "out", "顺带报个错"),  # 又成功又报错
        (True, None, None),
    ],
)
async def test_e2_invariant_holds_for_every_input_shape(ok: bool, output: Any, error: Any) -> None:
    evidence = await registry_for(ToolResult(ok=ok, output=output, error=error)).call(CALL)
    assert evidence.ok is (evidence.error is None)
    if not evidence.ok:
        assert evidence.error  # 失败必须有非空说明，否则上下文里读不出是为什么


async def test_e2_failure_without_reason_gets_a_deterministic_message() -> None:
    evidence = await registry_for(ToolResult(ok=False)).call(CALL)
    assert evidence.ok is False
    assert evidence.error == "tool echo failed without an error message"


async def test_e2_success_with_blank_error_keeps_the_output() -> None:
    """`error=""` 按"没报错"处理：反向选择会把本来可用的 output 折成一条错误。"""
    evidence = await registry_for(ToolResult(ok=True, output="usable", error="")).call(CALL)
    assert (evidence.ok, evidence.output, evidence.error) == (True, "usable", None)


async def test_e2_when_both_are_reported_failure_wins() -> None:
    """又给 output 又给 error：算失败。算成功等于把失败静默掉。"""
    evidence = await registry_for(ToolResult(ok=True, output="out", error="boom")).call(CALL)
    assert evidence.ok is False and evidence.error == "boom"


async def test_e2_sloppy_failure_reaches_the_context_not_as_none() -> None:
    """不变式真正的落点在上下文里：那条 tool 消息不能是字面量 "None"。

    `core/loop.py:121` 的投影是 `str(e.output if e.ok else e.error)`，L1 不许动它，
    所以只能在 Evidence 这一侧保证 `error` 非空。这条从 loop 外面验最终结果。
    """
    provider: Provider = FakeProvider(
        [ProviderResponse(content="thinking", tool_calls=[CALL]), final_response("done")]
    )
    agent = stableagent.Agent(
        name="e2", provider=provider, registry=registry_for(ToolResult(ok=False))
    )
    events = [event async for event in agent.stream("echo ping")]
    assert events[-1].type is stableagent.EventType.DONE
    tool_messages = [m for m in agent.last_state.messages if m.role == "tool"]
    assert [m.content for m in tool_messages] == ["tool echo failed without an error message"]


def test_e1_import_paths_point_at_one_class() -> None:
    """E1：搬家不改导入。三条路径必须是同一个类对象，不是三份副本。"""
    assert _ViaRegistry is _Canonical is stableagent.Evidence
    assert _Canonical.__module__ == "stableagent.tools.evidence"


def test_e1_construction_via_old_path_still_normalizes() -> None:
    """旧路径构造的对象走同一套归一化——重导出不是另造一个同名模型。"""
    evidence = _ViaRegistry(tool_call=CALL, ok=False)
    assert isinstance(evidence, _Canonical)
    assert evidence.error == "tool echo failed without an error message"


def test_e2_trace_fields_are_deferred_to_preserve_default_event_payload() -> None:
    """P3 不在 Evidence 加空追溯字段，避免默认 tool_result 事件偏离 P2 金标准。"""
    evidence = _Canonical(tool_call=CALL, ok=True, output="ok")
    assert set(evidence.model_dump()) == {"tool_call", "ok", "output", "error"}
    restored = _Canonical.model_validate_json(evidence.model_dump_json())
    assert restored == evidence
