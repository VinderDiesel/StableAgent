"""MCP Registry：server 描述符、tenant allowlist、启用/禁用与调用门闸（WP-7.3）。"""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from stableagent.integrations.errors import IntegrationsError


class McpRegistryError(IntegrationsError):
    def __init__(self, reason: str) -> None:
        super().__init__("mcp.registry", reason)


class McpServerDisabledError(McpRegistryError):
    def __init__(self, reason: str) -> None:
        IntegrationsError.__init__(self, "mcp.disabled", reason)


class McpTenantDeniedError(McpRegistryError):
    def __init__(self, reason: str) -> None:
        IntegrationsError.__init__(self, "mcp.tenant_denied", reason)


class McpNotAuthorizedError(McpRegistryError):
    def __init__(self, reason: str) -> None:
        IntegrationsError.__init__(self, "mcp.not_authorized", reason)


class McpTransportKind(str, Enum):
    STDIO = "stdio"
    HTTP = "http"
    SSE = "sse"


class McpRemoteSecurity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    require_tls: bool = True
    timeout_seconds: float = 30.0
    max_request_bytes: int = 1_048_576
    domain_allowlist: tuple[str, ...] = ()
    credential_ref: str | None = None


class McpServerDescriptor(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    version: str
    transport: McpTransportKind
    tenant_allowlist: frozenset[str] = Field(default_factory=frozenset)
    tool_schema_digest: str = ""
    health: Literal["ok", "degraded", "failed"] = "ok"
    credential_ref: str | None = None
    audit_level: Literal["minimal", "standard", "verbose"] = "standard"
    enabled: bool = False
    remote: McpRemoteSecurity | None = None
    command: tuple[str, ...] = ()


OperatorAuthorize = Callable[[str, str], bool]
"""(operator, action) -> allowed；action ∈ register|enable|disable。"""


class McpRegistry:
    """进程内注册表：discovery 与 invoke 分离；禁用阻断调用。"""

    def __init__(self, *, authorize: OperatorAuthorize | None = None) -> None:
        self._servers: dict[str, McpServerDescriptor] = {}
        self._authorize = authorize or (lambda _op, _action: True)
        self.audit_log: list[dict[str, Any]] = []

    def register(self, descriptor: McpServerDescriptor, *, operator: str) -> None:
        if not self._authorize(operator, "register"):
            raise McpNotAuthorizedError("register requires operator authorization")
        self._servers[descriptor.id] = descriptor
        self._audit(operator, "register", descriptor.id)

    def enable(self, server_id: str, *, operator: str) -> McpServerDescriptor:
        if not self._authorize(operator, "enable"):
            raise McpNotAuthorizedError("enable requires operator authorization")
        current = self._require(server_id)
        updated = current.model_copy(update={"enabled": True})
        self._servers[server_id] = updated
        self._audit(operator, "enable", server_id)
        return updated

    def disable(self, server_id: str, *, operator: str) -> McpServerDescriptor:
        if not self._authorize(operator, "disable"):
            raise McpNotAuthorizedError("disable requires operator authorization")
        current = self._require(server_id)
        updated = current.model_copy(update={"enabled": False})
        self._servers[server_id] = updated
        self._audit(operator, "disable", server_id)
        return updated

    def discover(self, *, tenant_id: str) -> list[McpServerDescriptor]:
        """Worker discovery：可见 ≠ 可调用；仅返回 allowlist 命中项。"""
        return [
            server
            for server in self._servers.values()
            if not server.tenant_allowlist or tenant_id in server.tenant_allowlist
        ]

    def assert_invokable(self, server_id: str, *, tenant_id: str) -> McpServerDescriptor:
        server = self._require(server_id)
        if server.tenant_allowlist and tenant_id not in server.tenant_allowlist:
            self._audit("system", "invoke_denied_tenant", server_id, tenant_id=tenant_id)
            raise McpTenantDeniedError(f"tenant {tenant_id} not allowed on {server_id}")
        if not server.enabled:
            self._audit("system", "invoke_denied_disabled", server_id, tenant_id=tenant_id)
            raise McpServerDisabledError(f"MCP server disabled: {server_id}")
        self._audit("system", "invoke_allowed", server_id, tenant_id=tenant_id)
        return server

    def get(self, server_id: str) -> McpServerDescriptor | None:
        return self._servers.get(server_id)

    def _require(self, server_id: str) -> McpServerDescriptor:
        server = self._servers.get(server_id)
        if server is None:
            raise McpRegistryError(f"unknown MCP server: {server_id}")
        return server

    def _audit(
        self, actor: str, action: str, server_id: str, **extra: Any
    ) -> None:
        self.audit_log.append(
            {"actor": actor, "action": action, "server_id": server_id, **extra}
        )
