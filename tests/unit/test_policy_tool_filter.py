"""L5 的 mcp_call 时机 + E3 的调用拒绝半边：可见集过滤与调用前授权。

授权在 Agent 视野之外仍然成立——即使 prompt 幻觉出一个工具名，守卫也不 delegate。
"""

from __future__ import annotations

import pathlib
from typing import Any

from stableagent.control.audit import AuditWriter
from stableagent.control.model import Action, ExplicitRequest
from stableagent.control.policy import StaticPolicy, StaticPolicyConfig
from stableagent.control.tool_filter import PolicyToolFilter, guard_mcp_tool
from stableagent.core.state import AgentState
from stableagent.orchestration.store import Store
from stableagent.tools.registry import Tool, ToolResult
from tests.support import FixedClock, SequenceIds

REPOSITORY = "acme/widgets"
GRANTABLE = frozenset(Action) - {Action.ENABLE_AUTO_MERGE}
MCP_KEYS = {"fs__read_file": "fs:read_file", "fs__drop_table": "fs:drop_table"}


def _counting_tool(
    name: str = "fs__read_file",
    *,
    description: str = "计数用替身",
    parameters: dict[str, Any] | None = None,
) -> tuple[Tool, list[int]]:
    """Tool 的 name/description/parameters 是 ClassVar：动态造子类，计数放闭包里。"""
    calls: list[int] = []

    async def execute(self: Tool, arguments: dict[str, Any]) -> ToolResult:
        calls.append(1)
        return ToolResult(ok=True, output="done")

    namespace = {
        "name": name,
        "description": description,
        "parameters": parameters or {"type": "object", "properties": {}},
        "execute": execute,
    }
    return type(f"Counting_{name}", (Tool,), namespace)(), calls


def _rig(tmp_path: pathlib.Path, *, allowed_mcp: list[str], expires_at: int | None = None):
    clock = FixedClock()
    ids = SequenceIds()
    store = Store(tmp_path / "control.db", clock=clock)
    config = StaticPolicyConfig(
        repository=REPOSITORY,
        actors=frozenset({"octocat"}),
        grantable_actions=GRANTABLE,
        allowed_mcp=["fs:read_file", "fs:drop_table"],
        default_validity_ms=60_000,
    )
    policy = StaticPolicy(config, clock=clock, ids=ids)
    audit = AuditWriter(policy, store, clock=clock, ids=ids)
    admission = policy.admit_explicit(
        ExplicitRequest(
            repository=REPOSITORY,
            requester="octocat",
            mentioned=True,
            stable_id="evt-1",
            text="@agent 读文件",
        )
    )
    mandate = policy.create_mandate(
        admission, actions=[Action.CALL_MCP_TOOL], allowed_mcp=allowed_mcp
    )
    if expires_at is not None:
        mandate = mandate.model_copy(update={"expires_at": expires_at})
    store.insert_mandate(mandate)
    return mandate, policy, audit, store


def _state() -> AgentState:
    return AgentState(run_id="r", agent_name="a")


def _guard(inner: Tool, mandate, policy, audit, *, tool_name: str = "read_file", ticket_id="tk-1"):
    return guard_mcp_tool(
        inner,
        server="fs",
        tool_name=tool_name,
        mandate=mandate,
        policy=policy,
        audit=audit,
        ticket_id=ticket_id,
    )


def test_filter_hides_mcp_tools_outside_the_mandate(tmp_path: pathlib.Path) -> None:
    mandate, _, _, _ = _rig(tmp_path, allowed_mcp=["fs:read_file"])
    tool_filter = PolicyToolFilter(mandate, mcp_keys=MCP_KEYS)
    specs = [{"name": "fs__read_file"}, {"name": "fs__drop_table"}, {"name": "local_tool"}]
    kept = tool_filter.select(specs, _state())
    assert [spec["name"] for spec in kept] == ["fs__read_file", "local_tool"]


def test_filter_with_empty_mandate_hides_all_mcp_tools(tmp_path: pathlib.Path) -> None:
    mandate, _, _, _ = _rig(tmp_path, allowed_mcp=[])
    tool_filter = PolicyToolFilter(mandate, mcp_keys=MCP_KEYS)
    kept = tool_filter.select([{"name": name} for name in MCP_KEYS], _state())
    assert kept == []


def test_filter_is_usable_as_the_p3_tool_selector_seam(tmp_path: pathlib.Path) -> None:
    mandate, _, _, _ = _rig(tmp_path, allowed_mcp=["fs:read_file"])
    selector = PolicyToolFilter(mandate, mcp_keys=MCP_KEYS).as_tool_selector()
    assert [spec["name"] for spec in selector([{"name": "fs__drop_table"}], _state())] == []


async def test_guarded_tool_denies_without_calling_the_session(tmp_path: pathlib.Path) -> None:
    mandate, policy, audit, store = _rig(tmp_path, allowed_mcp=[])
    inner, calls = _counting_tool()
    result = await _guard(inner, mandate, policy, audit).execute({})
    assert result.ok is False and "mcp_not_allowed" in (result.error or "")
    assert calls == []
    rows = store.list_audit(ticket_id="tk-1")
    assert [row["kind"] for row in rows] == ["revalidate", "authorize"]
    assert rows[0]["action"] == "mcp_call"
    assert rows[1]["decision"] == "deny"


async def test_guarded_tool_allows_and_audits_when_granted(tmp_path: pathlib.Path) -> None:
    mandate, policy, audit, store = _rig(tmp_path, allowed_mcp=["fs:read_file"])
    inner, calls = _counting_tool()
    assert (await _guard(inner, mandate, policy, audit).execute({})).ok is True
    assert calls == [1]
    rows = store.list_audit(ticket_id="tk-1")
    assert [row["kind"] for row in rows] == ["revalidate", "authorize"]
    assert rows[1]["decision"] == "allow"


async def test_hallucinated_mcp_name_never_reaches_the_session(tmp_path: pathlib.Path) -> None:
    mandate, policy, audit, store = _rig(tmp_path, allowed_mcp=["fs:read_file"])
    inner, calls = _counting_tool("fs__ghost")
    result = await _guard(inner, mandate, policy, audit, tool_name="ghost").execute({})
    assert result.ok is False
    assert calls == []


async def test_expired_mandate_is_denied_before_the_call(tmp_path: pathlib.Path) -> None:
    mandate, policy, audit, store = _rig(tmp_path, allowed_mcp=["fs:read_file"], expires_at=1)
    inner, calls = _counting_tool()
    result = await _guard(inner, mandate, policy, audit).execute({})
    assert result.ok is False and "mandate_expired" in (result.error or "")
    assert calls == []
    rows = store.list_audit(ticket_id="tk-1")
    assert [row["kind"] for row in rows] == ["revalidate"]  # 过期即止，不再 authorize


def test_guard_preserves_the_tool_contract(tmp_path: pathlib.Path) -> None:
    mandate, policy, audit, store = _rig(tmp_path, allowed_mcp=["fs:read_file"])
    parameters = {"type": "object", "properties": {"path": {"type": "string"}}}
    inner, _ = _counting_tool(description="读文件", parameters=parameters)
    guarded = _guard(inner, mandate, policy, audit)
    assert guarded.name == inner.name
    assert guarded.description == inner.description
    assert guarded.parameters == parameters
    assert isinstance(guarded, Tool)
