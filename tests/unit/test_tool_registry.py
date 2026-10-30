"""契约五：Tool / ToolRegistry / Evidence 失败归一化。"""

from typing import Any

import pytest

from stableagent.providers.base import ToolCall
from stableagent.tools.registry import Evidence, Tool, ToolRegistry, ToolResult


class EchoTool(Tool):
    name = "echo"
    description = "echo back the input"
    parameters = {"type": "object", "properties": {"text": {"type": "string"}}}

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        return ToolResult(output=arguments.get("text", ""))


class BoomTool(Tool):
    name = "boom"
    description = "always raises"

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        raise RuntimeError("boom")


class NoNameTool(Tool):
    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        return ToolResult()


def _call(name: str) -> ToolCall:
    return ToolCall(id="c-1", name=name, arguments={"text": "ping"})


def test_registry_rejects_duplicate_and_nameless_tools() -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())
    with pytest.raises(ValueError):
        registry.register(EchoTool())
    with pytest.raises(ValueError):
        registry.register(NoNameTool())


def test_specs_expose_tool_schema() -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())
    specs = registry.specs()
    assert specs[0]["name"] == "echo"
    assert "text" in specs[0]["parameters"]["properties"]


async def test_successful_call_produces_evidence() -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())
    evidence = await registry.call(_call("echo"))
    assert isinstance(evidence, Evidence)
    assert evidence.ok is True
    assert evidence.output == "ping"
    assert evidence.error is None


async def test_exceptions_are_normalized_to_failed_evidence() -> None:
    registry = ToolRegistry()
    registry.register(BoomTool())
    evidence = await registry.call(_call("boom"))
    assert evidence.ok is False
    assert evidence.error is not None and "RuntimeError" in evidence.error
    assert evidence.tool_call.id == "c-1"


async def test_unregistered_tool_fails_gracefully() -> None:
    evidence = await ToolRegistry().call(_call("missing"))
    assert evidence.ok is False
    assert "KeyError" in (evidence.error or "")
