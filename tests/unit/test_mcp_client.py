"""MCP 客户端（P5 M3）：fake transport 撑 E1，真子进程撑 framing 与环境最小化。

Task 2 只覆盖 transport；session/adapter 在 Task 3/4 追加到本文件。
"""

from __future__ import annotations

import pathlib
import sys

import pytest

from stableagent.integrations.errors import (
    McpProcessExitedError,
    McpProtocolError,
    McpTimeoutError,
)
from stableagent.integrations.mcp import StdioMcpTransport


class FakeMcpTransport:
    """E1 的事实源：脚本化应答 + 调用计数，拒绝必须表现为"没有下一次调用"。"""

    def __init__(self, replies: list[dict] | None = None, *, fail: Exception | None = None) -> None:
        self.replies = list(replies or [])
        self.fail = fail
        self.calls: list[tuple[str, dict]] = []
        self.closed = False

    async def request(self, method: str, params: dict) -> dict:
        self.calls.append((method, params))
        if self.fail is not None:
            raise self.fail
        return self.replies.pop(0)

    async def notify(self, method: str, params: dict) -> None:
        self.calls.append((method, params))

    async def close(self) -> None:
        self.closed = True


def _server_script(tmp_path: pathlib.Path, body: str) -> list[str]:
    script = tmp_path / "server.py"
    script.write_text(body, encoding="utf-8")
    return [sys.executable, str(script)]


_ECHO_SERVER = (
    "import json,sys\n"
    "for line in sys.stdin:\n"
    "    req=json.loads(line)\n"
    "    sys.stdout.write(json.dumps("
    "{'jsonrpc':'2.0','id':req['id'],'result':{'ok':True}})+'\\n')\n"
    "    sys.stdout.flush()\n"
)


async def test_stdio_transport_round_trips_with_matching_ids(tmp_path: pathlib.Path) -> None:
    transport = StdioMcpTransport(_server_script(tmp_path, _ECHO_SERVER), timeout_s=10)
    try:
        first = await transport.request("initialize", {})
        second = await transport.request("tools/list", {})
        assert first == {"ok": True} and second == {"ok": True}
    finally:
        await transport.close()


async def test_stdio_transport_rejects_mismatched_id(tmp_path: pathlib.Path) -> None:
    body = (
        "import json,sys\n"
        "for line in sys.stdin:\n"
        "    sys.stdout.write(json.dumps({'jsonrpc':'2.0','id':999,'result':{}})+'\\n')\n"
        "    sys.stdout.flush()\n"
    )
    transport = StdioMcpTransport(_server_script(tmp_path, body), timeout_s=10)
    try:
        with pytest.raises(McpProtocolError):
            await transport.request("initialize", {})
    finally:
        await transport.close()


async def test_stdio_transport_rejects_jsonrpc_error(tmp_path: pathlib.Path) -> None:
    body = (
        "import json,sys\n"
        "for line in sys.stdin:\n"
        "    req=json.loads(line)\n"
        "    sys.stdout.write(json.dumps({'jsonrpc':'2.0','id':req['id'],"
        "'error':{'code':-32601,'message':'no method'}})+'\\n')\n"
        "    sys.stdout.flush()\n"
    )
    transport = StdioMcpTransport(_server_script(tmp_path, body), timeout_s=10)
    try:
        with pytest.raises(McpProtocolError, match="JSON-RPC error"):
            await transport.request("nope", {})
    finally:
        await transport.close()


async def test_stdio_transport_times_out(tmp_path: pathlib.Path) -> None:
    transport = StdioMcpTransport(
        _server_script(tmp_path, "import time\ntime.sleep(30)\n"), timeout_s=0.2
    )
    try:
        with pytest.raises(McpTimeoutError):
            await transport.request("initialize", {})
    finally:
        await transport.close()


async def test_stdio_transport_reports_process_exit(tmp_path: pathlib.Path) -> None:
    transport = StdioMcpTransport(_server_script(tmp_path, "raise SystemExit(3)\n"), timeout_s=10)
    try:
        with pytest.raises(McpProcessExitedError):
            await transport.request("initialize", {})
    finally:
        await transport.close()


async def test_stdio_transport_default_env_is_minimal(tmp_path: pathlib.Path) -> None:
    body = (
        "import json,os,sys\n"
        "for line in sys.stdin:\n"
        "    req=json.loads(line)\n"
        "    sys.stdout.write(json.dumps({'jsonrpc':'2.0','id':req['id'],"
        "'result':{'keys':sorted(os.environ)}})+'\\n')\n"
        "    sys.stdout.flush()\n"
    )
    transport = StdioMcpTransport(_server_script(tmp_path, body), timeout_s=10)
    try:
        seen = set((await transport.request("initialize", {}))["keys"])
        # 宿主秘密一律不继承；平台自己注入的键（macOS 的 LC_CTYPE 等）不在本断言射程。
        assert not {key for key in seen if "TOKEN" in key or "SECRET" in key or "KEY" in key}
        assert "PATH" in seen and "LANG" in seen
        assert "PYTHONPATH" not in seen
    finally:
        await transport.close()


async def test_stdio_transport_notify_does_not_wait_for_a_reply(tmp_path: pathlib.Path) -> None:
    body = (
        "import json,sys\n"
        "for line in sys.stdin:\n"
        "    req=json.loads(line)\n"
        "    if 'id' not in req:\n"
        "        continue\n"
        "    sys.stdout.write(json.dumps("
        "{'jsonrpc':'2.0','id':req['id'],'result':{'ok':True}})+'\\n')\n"
        "    sys.stdout.flush()\n"
    )
    transport = StdioMcpTransport(_server_script(tmp_path, body), timeout_s=10)
    try:
        await transport.notify("notifications/initialized", {})
        assert await transport.request("initialize", {}) == {"ok": True}
    finally:
        await transport.close()


async def test_stdio_transport_close_is_idempotent(tmp_path: pathlib.Path) -> None:
    transport = StdioMcpTransport(_server_script(tmp_path, _ECHO_SERVER), timeout_s=10)
    await transport.request("initialize", {})
    await transport.close()
    await transport.close()


def test_stdio_transport_rejects_empty_command() -> None:
    with pytest.raises(McpProtocolError):
        StdioMcpTransport([])


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        ("not json at all", "不是合法 JSON"),
        ('{"jsonrpc":"2.0","id":1,"result":[1,2]}', "result 不是对象"),
    ],
)
async def test_stdio_transport_rejects_malformed_replies(
    tmp_path: pathlib.Path, payload: str, match: str
) -> None:
    body = (
        "import sys\n"
        "for line in sys.stdin:\n"
        f"    sys.stdout.write({payload!r} + '\\n')\n"
        "    sys.stdout.flush()\n"
    )
    transport = StdioMcpTransport(_server_script(tmp_path, body), timeout_s=10)
    try:
        with pytest.raises(McpProtocolError, match=match):
            await transport.request("initialize", {})
    finally:
        await transport.close()
