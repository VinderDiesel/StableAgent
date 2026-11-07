"""MCP 的授权面：可见集在过滤器求交，调用判定在守卫执行（ADR-05 决策 8）。

分成两半是因为它们回答不同问题：过滤器决定 Agent 看得见什么（省 token、少幻觉），
守卫决定看得见的东西能不能真的调用——幻觉出的工具名绕过视野，但绕不过守卫。
本模块只认识 control/orchestration/core/context/tools，不认识 execution 与 integrations：
协议适配留在 `integrations/mcp.py`，授权判定留在这里。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from stableagent.context.capability import ToolSelector
from stableagent.control.audit import AuditWriter
from stableagent.control.model import Action, Decision, Resource
from stableagent.control.policy import StaticPolicy
from stableagent.core.state import AgentState
from stableagent.orchestration.model import Mandate
from stableagent.tools.registry import Tool, ToolResult


class PolicyToolFilter:
    """P3 `tool_selector` 接缝上的授权过滤：只裁 MCP 工具，本地工具原样放行。

    不在 `mcp_keys` 里的 spec 一律保留——P5 的工具授权面只覆盖 MCP 与命令，
    命令走 `execution/command.py` 的 allowlist，不在能力视图里裁。
    """

    def __init__(self, mandate: Mandate, *, mcp_keys: Mapping[str, str]) -> None:
        self._mandate = mandate
        self._mcp_keys = dict(mcp_keys)

    def select(self, specs: list[dict[str, Any]], state: AgentState) -> list[dict[str, Any]]:
        allowed = set(self._mandate.allowed_mcp)
        kept: list[dict[str, Any]] = []
        for spec in specs:
            key = self._mcp_keys.get(spec.get("name", ""))
            if key is None or key in allowed:
                kept.append(spec)
        return kept

    def as_tool_selector(self) -> ToolSelector:
        return self.select


def guard_mcp_tool(
    tool: Tool,
    *,
    server: str,
    tool_name: str,
    mandate: Mandate,
    policy: StaticPolicy,
    audit: AuditWriter,
    ticket_id: str | None = None,
    assignment_id: str | None = None,
) -> Tool:
    """把一次 MCP 调用包成"先复检、再授权、才 delegate"的同形 Tool。

    复检不通过就止于复检（不再 authorize）：过期/版本漂移意味着这份 Mandate 已经不作数，
    继续判资源只会往审计里塞一行没有意义的 deny。
    """
    resource = Resource(kind="mcp", server=server, tool=tool_name)

    async def execute(self: Tool, arguments: dict[str, Any]) -> ToolResult:
        revalidation = policy.revalidate(mandate, when="mcp_call")
        audit.revalidated(
            mandate,
            "mcp_call",
            revalidation,
            ticket_id=ticket_id,
            assignment_id=assignment_id,
        )
        denied = _denial(revalidation, server, tool_name)
        if denied is not None:
            return denied
        decision = policy.authorize(mandate, Action.CALL_MCP_TOOL, resource)
        audit.authorized(
            mandate,
            Action.CALL_MCP_TOOL,
            resource,
            decision,
            ticket_id=ticket_id,
            assignment_id=assignment_id,
        )
        denied = _denial(decision, server, tool_name)
        if denied is not None:
            return denied
        return await tool.execute(arguments)

    # Tool 的 name/description/parameters 是 ClassVar：动态造子类，不给 ClassVar 赋实例值。
    namespace = {
        "name": tool.name,
        "description": tool.description,
        "parameters": tool.parameters,
        "execute": execute,
    }
    return type(f"Guarded_{tool.name}", (Tool,), namespace)()


def _denial(decision: Decision, server: str, tool_name: str) -> ToolResult | None:
    if decision.allowed:
        return None
    return ToolResult(ok=False, error=f"{decision.reason}:{server}:{tool_name}")
