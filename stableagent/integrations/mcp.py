"""MCP 客户端（P5 M3，ADR-05 决策 8）：第一版只做 stdio JSON-RPC。

本模块只产出既有 `Tool`，不认识授权层——调用前的 revalidate/authorize/审计由
`control.tool_filter` 包在 Tool 外层完成，所以这里没有 control/execution 的 import。
HTTP/SSE transport 留 P7：`McpTransport` 协议不绑定 stdio，换实现不需要改 session。
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field

from stableagent.integrations.errors import (
    McpProcessExitedError,
    McpProtocolError,
    McpSchemaError,
    McpTimeoutError,
)
from stableagent.tools.registry import Tool, ToolResult

MCP_CHILD_LANG = "C.UTF-8"
_CLOSE_TIMEOUT_S = 5.0
MCP_PROTOCOL_VERSION = "2024-11-05"
_DEFAULT_INPUT_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}}


class McpTransport(Protocol):
    """请求/应答传输的窄面：id 与信封校验归实现，session 只认 result 字典。"""

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]: ...

    async def notify(self, method: str, params: dict[str, Any]) -> None: ...

    async def close(self) -> None: ...


class StdioMcpTransport:
    """参数数组启动子进程，换行分帧；环境默认只给 PATH 与固定 LANG。

    `env=` 是调用方显式给的表（本类不额外继承宿主）；缺省时宿主秘密一律不进孩子。
    """

    def __init__(
        self,
        command: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        cwd: Path | None = None,
        timeout_s: float = 30.0,
    ) -> None:
        if not command:
            raise McpProtocolError("command 不能为空")
        self._command = list(command)
        self._env = (
            dict(env)
            if env is not None
            else {"PATH": os.environ.get("PATH", ""), "LANG": MCP_CHILD_LANG}
        )
        self._cwd = cwd
        self._timeout_s = timeout_s
        self._next_id = 0
        self._process: asyncio.subprocess.Process | None = None

    async def _ensure_started(self) -> asyncio.subprocess.Process:
        if self._process is None:
            self._process = await asyncio.create_subprocess_exec(
                *self._command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._env,
                cwd=str(self._cwd) if self._cwd is not None else None,
            )
        return self._process

    async def _write(self, process: asyncio.subprocess.Process, message: dict[str, Any]) -> None:
        assert process.stdin is not None
        process.stdin.write((json.dumps(message, ensure_ascii=False) + "\n").encode())
        await process.stdin.drain()

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        process = await self._ensure_started()
        self._next_id += 1
        request_id = self._next_id
        await self._write(
            process,
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
        )
        assert process.stdout is not None
        try:
            line = await asyncio.wait_for(process.stdout.readline(), self._timeout_s)
        except asyncio.TimeoutError as exc:
            raise McpTimeoutError(method, self._timeout_s) from exc
        if not line:
            raise McpProcessExitedError(process.returncode)
        try:
            envelope = json.loads(line)
        except json.JSONDecodeError as exc:
            raise McpProtocolError(f"{method} 应答不是合法 JSON") from exc
        if envelope.get("jsonrpc") != "2.0" or envelope.get("id") != request_id:
            raise McpProtocolError(f"{method} 应答的 jsonrpc/id 不匹配")
        if "error" in envelope:
            raise McpProtocolError(f"{method} 返回 JSON-RPC error: {envelope['error']}")
        result = envelope.get("result")
        if not isinstance(result, dict):
            raise McpProtocolError(f"{method} 的 result 不是对象")
        return result

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        process = await self._ensure_started()
        await self._write(process, {"jsonrpc": "2.0", "method": method, "params": params})

    async def close(self) -> None:
        process = self._process
        self._process = None
        if process is None or process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), _CLOSE_TIMEOUT_S)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()


class McpToolSpec(BaseModel):
    """`tools/list` 的一项：发现结果只进诊断与授权交集，不直接进 capability view。"""

    name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=lambda: dict(_DEFAULT_INPUT_SCHEMA))


class McpSession:
    """initialize / tools/list / tools/call 三个方法；信封与 id 校验归 transport。"""

    def __init__(
        self,
        transport: McpTransport,
        *,
        client_name: str = "stableagent",
        client_version: str = "0.1.0",
    ) -> None:
        self._transport = transport
        self._client_name = client_name
        self._client_version = client_version

    async def initialize(self) -> dict[str, Any]:
        result = await self._transport.request(
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": self._client_name, "version": self._client_version},
            },
        )
        await self._transport.notify("notifications/initialized", {})
        return result

    async def list_tools(self) -> list[McpToolSpec]:
        result = await self._transport.request("tools/list", {})
        tools = result.get("tools")
        if not isinstance(tools, list):
            raise McpSchemaError("tools/list", "tools 不是数组")
        specs = []
        for item in tools:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                raise McpSchemaError("tools/list", "工具项缺少字符串 name")
            if not item["name"]:
                raise McpSchemaError("tools/list", "工具 name 为空")
            schema = item.get("inputSchema", _DEFAULT_INPUT_SCHEMA)
            if not isinstance(schema, dict):
                raise McpSchemaError("tools/list", "inputSchema 不是对象")
            specs.append(
                McpToolSpec(
                    name=item["name"],
                    description=str(item.get("description", "")),
                    input_schema=schema,
                )
            )
        return specs

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        result = await self._transport.request("tools/call", {"name": name, "arguments": arguments})
        content = result.get("content")
        if not isinstance(content, list):
            raise McpSchemaError("tools/call", "content 不是数组")
        text = "\n".join(
            part["text"]
            for part in content
            if isinstance(part, dict) and part.get("type") == "text" and "text" in part
        )
        if result.get("isError"):
            return ToolResult(ok=False, error=text or "mcp tool failed without an error message")
        return ToolResult(ok=True, output=text)


def mcp_tool_name(server: str, tool: str) -> str:
    """Provider 侧工具名：OpenAI function name 只接受 [a-zA-Z0-9_-]，故用双下划线。"""
    return f"{server}__{tool}"


def mcp_allowlist_key(server: str, tool: str) -> str:
    """Mandate.allowed_mcp 的键形状（control/policy.py 的 Resource kind="mcp" 口径）。"""
    return f"{server}:{tool}"


class McpToolAdapter:
    """发现 ≠ 授权：可见集 = `tools/list ∩ allowed`，未列入者连 Tool 对象都不产出。

    调用前的 revalidate/authorize/审计不在这里——由 `control.tool_filter.guard_mcp_tool`
    包在外层完成，本类因此不需要认识授权层（ADR-05 决策 8）。
    """

    def __init__(self, session: McpSession, *, server: str, allowed: frozenset[str]) -> None:
        self._session = session
        self._server = server
        self._allowed = allowed
        self._keys: dict[str, str] = {}

    def keys(self) -> dict[str, str]:
        """工具名 → allowlist 键的映射表；filter 只吃这张表，不做字符串反解。"""
        return dict(self._keys)

    async def tools(self) -> list[Tool]:
        specs = await self._session.list_tools()
        tools: list[Tool] = []
        self._keys = {}
        for spec in specs:
            key = mcp_allowlist_key(self._server, spec.name)
            if key not in self._allowed:
                continue
            name = mcp_tool_name(self._server, spec.name)
            self._keys[name] = key
            tools.append(self._build(name, spec))
        return tools

    def _build(self, name: str, spec: McpToolSpec) -> Tool:
        session = self._session
        tool_name = spec.name

        async def execute(self: Tool, arguments: dict[str, Any]) -> ToolResult:
            return await session.call_tool(tool_name, arguments)

        # Tool 的 name/description/parameters 是 ClassVar：动态造子类，不给 ClassVar 赋实例值。
        namespace = {
            "name": name,
            "description": spec.description,
            "parameters": spec.input_schema,
            "execute": execute,
        }
        tool_class = type(f"McpTool_{name}", (Tool,), namespace)
        return tool_class()
