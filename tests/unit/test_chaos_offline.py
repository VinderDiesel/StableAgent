"""P7 M6：离线 chaos / failure 演练（无真实网络/Docker）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from stableagent.deploy_config import DeployConfig
from stableagent.integrations.mcp_registry import (
    McpRegistry,
    McpServerDescriptor,
    McpServerDisabledError,
    McpTransportKind,
)
from stableagent.memory.model import MemoryAuthContext, MemoryScope, MemoryScopeKind
from stableagent.memory.service import MemoryService
from stableagent.memory.store import MemoryStore
from stableagent.supervisor.auto import AutoRule
from stableagent.supervisor.model import SourceCursor, SourceHealth
from stableagent.supervisor.store import SupervisorStore
from tests.support import FixedClock, SequenceIds


def test_mcp_disabled_blocks_invoke() -> None:
    reg = McpRegistry()
    reg.register(
        McpServerDescriptor(
            id="s1",
            version="1",
            transport=McpTransportKind.STDIO,
            tenant_allowlist=frozenset({"t"}),
            enabled=True,
            command=("echo",),
        ),
        operator="admin",
    )
    reg.disable("s1", operator="admin")
    with pytest.raises(McpServerDisabledError):
        reg.assert_invokable("s1", tenant_id="t")


def test_memory_degrades_on_failure(tmp_path: Path) -> None:
    clock = FixedClock(1)
    store = MemoryStore(tmp_path / "m.db", clock=clock)
    svc = MemoryService(store, clock=clock, ids=SequenceIds())

    def boom(*_a: object, **_k: object) -> list[object]:
        raise RuntimeError("index down")

    svc._retriever.retrieve = boom  # type: ignore[method-assign]  # noqa: SLF001
    auth = MemoryAuthContext(
        tenant_id="t",
        allowed_scopes=frozenset(
            {MemoryScope(kind=MemoryScopeKind.TICKET, id="tk")}
        ),
    )
    assert svc.retrieve("q", auth) == []
    assert svc.degraded_events


def test_source_health_failed_cursor(tmp_path: Path) -> None:
    clock = FixedClock(1)
    store = SupervisorStore(tmp_path / "s.db", clock=clock)
    with store.transaction():
        store.put_source_cursor(
            SourceCursor(
                source="github",
                cursor="c1",
                health=SourceHealth.FAILED,
                consecutive_failures=3,
                tenant_id="default",
            )
        )
    cursors = store.list_source_cursors()
    assert cursors[0].health is SourceHealth.FAILED


def test_deploy_and_auto_rule_schema() -> None:
    cfg = DeployConfig(tenant_id="default", state_dir="./data", secret_refs=("gh_token",))
    assert cfg.listen_port == 8080
    AutoRule.model_validate(
        {"id": "r", "tenant_id": "default", "repository": "o/r", "enabled": False}
    )
    with pytest.raises(ValueError):
        DeployConfig(state_dir="../escape")
