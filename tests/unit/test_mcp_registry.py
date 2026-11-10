"""P7 M3：McpRegistry 启用/禁用、tenant allowlist、discovery ≠ invoke。"""

from __future__ import annotations

import pytest

from stableagent.integrations.mcp_registry import (
    McpNotAuthorizedError,
    McpRegistry,
    McpServerDescriptor,
    McpServerDisabledError,
    McpTenantDeniedError,
    McpTransportKind,
)


def _desc(**kwargs: object) -> McpServerDescriptor:
    base = {
        "id": "srv-1",
        "version": "1.0.0",
        "transport": McpTransportKind.STDIO,
        "tenant_allowlist": frozenset({"tenant-a"}),
        "enabled": False,
        "command": ("echo",),
    }
    base.update(kwargs)
    return McpServerDescriptor.model_validate(base)


def test_disable_blocks_invoke_and_audits() -> None:
    registry = McpRegistry()
    registry.register(_desc(), operator="admin")
    registry.enable("srv-1", operator="admin")
    assert registry.assert_invokable("srv-1", tenant_id="tenant-a").enabled is True
    registry.disable("srv-1", operator="admin")
    with pytest.raises(McpServerDisabledError):
        registry.assert_invokable("srv-1", tenant_id="tenant-a")
    actions = [e["action"] for e in registry.audit_log]
    assert "disable" in actions
    assert "invoke_denied_disabled" in actions


def test_discovery_does_not_imply_invoke() -> None:
    registry = McpRegistry()
    registry.register(_desc(enabled=False), operator="admin")
    visible = registry.discover(tenant_id="tenant-a")
    assert len(visible) == 1
    with pytest.raises(McpServerDisabledError):
        registry.assert_invokable("srv-1", tenant_id="tenant-a")


def test_tenant_b_cannot_use_tenant_a_server() -> None:
    registry = McpRegistry()
    registry.register(_desc(enabled=True), operator="admin")
    with pytest.raises(McpTenantDeniedError):
        registry.assert_invokable("srv-1", tenant_id="tenant-b")


def test_operator_authorization_required() -> None:
    registry = McpRegistry(authorize=lambda _op, _action: False)
    with pytest.raises(McpNotAuthorizedError):
        registry.register(_desc(), operator="nobody")
