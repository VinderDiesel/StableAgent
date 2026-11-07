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

from stableagent.integrations.errors import (
    McpProcessExitedError,
    McpProtocolError,
    McpTimeoutError,
)

MCP_CHILD_LANG = "C.UTF-8"
_CLOSE_TIMEOUT_S = 5.0


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
