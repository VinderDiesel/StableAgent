"""E2/E3：真 stdio 换行分帧跑通，且「server 暴露了什么」≠「Agent 能调用什么」。

fixture 故意让 `secret` 也出现在 tools/list 且 tools/call 照常回显：拒绝必须发生在我们
这一侧（McpToolAdapter 的可见集 + PolicyToolFilter），不能依赖 server 自觉。
全程标准库子进程，无公网、无 Docker。
"""

from __future__ import annotations

import pathlib
import sys

from stableagent.control.model import Action, ExplicitRequest
from stableagent.control.policy import StaticPolicy, StaticPolicyConfig
from stableagent.control.tool_filter import PolicyToolFilter
from stableagent.core.state import AgentState
from stableagent.integrations.mcp import McpSession, McpToolAdapter, StdioMcpTransport
from stableagent.orchestration.model import Mandate
from tests.support import FixedClock, SequenceIds

FIXTURE = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "mcp_echo_server.py"


def _mandate(*, allowed_mcp: list[str]) -> Mandate:
    clock = FixedClock()
    policy = StaticPolicy(
        StaticPolicyConfig(
            repository="acme/widgets",
            actors=frozenset({"octocat"}),
            grantable_actions=frozenset(Action) - {Action.ENABLE_AUTO_MERGE},
            allowed_mcp=["echo:echo", "echo:secret"],
            default_validity_ms=60_000,
        ),
        clock=clock,
        ids=SequenceIds(),
    )
    admission = policy.admit_explicit(
        ExplicitRequest(
            repository="acme/widgets",
            requester="octocat",
            mentioned=True,
            stable_id="evt-1",
            text="@agent 试一下",
        )
    )
    return policy.create_mandate(admission, actions=[Action.CALL_MCP_TOOL], allowed_mcp=allowed_mcp)


async def test_real_stdio_framing_end_to_end() -> None:
    transport = StdioMcpTransport([sys.executable, str(FIXTURE)], timeout_s=10)
    try:
        session = McpSession(transport)
        await session.initialize()
        specs = await session.list_tools()
        assert {spec.name for spec in specs} == {"echo", "secret"}
        result = await session.call_tool("echo", {"text": "hi"})
        assert result.ok is True
        assert "hi" in str(result.output)
    finally:
        await transport.close()


async def test_discovery_is_not_authorization() -> None:
    transport = StdioMcpTransport([sys.executable, str(FIXTURE)], timeout_s=10)
    try:
        session = McpSession(transport)
        await session.initialize()
        adapter = McpToolAdapter(session, server="echo", allowed=frozenset({"echo:echo"}))
        tools = await adapter.tools()
        assert [tool.name for tool in tools] == ["echo__echo"]
        tool_filter = PolicyToolFilter(_mandate(allowed_mcp=["echo:echo"]), mcp_keys=adapter.keys())
        specs = [{"name": tool.name} for tool in tools] + [{"name": "echo__secret"}]
        visible = tool_filter.select(specs, AgentState(run_id="r", agent_name="a"))
        assert [spec["name"] for spec in visible] == ["echo__echo"]
        # server 侧对 secret 也照常回显：可调用 ≠ 已授权，裁决权必须在客户端
        echoed = await session.call_tool("secret", {"text": "should not be visible"})
        assert echoed.ok is True
    finally:
        await transport.close()
