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
    McpSchemaError,
    McpTimeoutError,
)
from stableagent.integrations.mcp import McpSession, StdioMcpTransport


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


# ---------------------------------------------------------------- Task 3：McpSession（E1）


async def test_session_initialize_sends_client_info_and_initialized_notification() -> None:
    transport = FakeMcpTransport([{"protocolVersion": "2024-11-05", "capabilities": {}}])
    session = McpSession(transport)
    result = await session.initialize()
    assert result["protocolVersion"] == "2024-11-05"
    assert transport.calls[0][0] == "initialize"
    assert transport.calls[0][1]["clientInfo"]["name"] == "stableagent"
    assert transport.calls[1] == ("notifications/initialized", {})


async def test_session_list_tools_parses_specs() -> None:
    transport = FakeMcpTransport(
        [{"tools": [{"name": "read_file", "description": "d", "inputSchema": {"type": "object"}}]}]
    )
    specs = await McpSession(transport).list_tools()
    assert specs[0].name == "read_file"
    assert specs[0].description == "d"
    assert specs[0].input_schema == {"type": "object"}
    assert transport.calls[-1][0] == "tools/list"


async def test_session_list_tools_defaults_missing_schema() -> None:
    transport = FakeMcpTransport([{"tools": [{"name": "echo"}]}])
    specs = await McpSession(transport).list_tools()
    assert specs[0].input_schema == {"type": "object", "properties": {}}


async def test_session_call_tool_maps_content_and_is_error() -> None:
    transport = FakeMcpTransport([{"content": [{"type": "text", "text": "hello"}]}])
    result = await McpSession(transport).call_tool("read_file", {"path": "a"})
    assert result.ok is True and result.output == "hello"
    assert transport.calls[-1] == (
        "tools/call",
        {"name": "read_file", "arguments": {"path": "a"}},
    )
    failed = FakeMcpTransport([{"content": [{"type": "text", "text": "boom"}], "isError": True}])
    bad = await McpSession(failed).call_tool("read_file", {})
    assert bad.ok is False and bad.error == "boom"


async def test_session_call_tool_joins_multiple_text_parts() -> None:
    transport = FakeMcpTransport(
        [
            {
                "content": [
                    {"type": "text", "text": "a"},
                    {"type": "image", "data": "x"},
                    {"type": "text", "text": "b"},
                ]
            }
        ]
    )
    result = await McpSession(transport).call_tool("echo", {})
    assert result.output == "a\nb"


async def test_session_propagates_jsonrpc_error() -> None:
    transport = FakeMcpTransport(fail=McpProtocolError("JSON-RPC error: -32601"))
    with pytest.raises(McpProtocolError):
        await McpSession(transport).list_tools()


async def test_session_propagates_timeout_and_process_exit() -> None:
    for error in (McpTimeoutError("tools/list", 1.0), McpProcessExitedError(1)):
        with pytest.raises(type(error)):
            await McpSession(FakeMcpTransport(fail=error)).list_tools()


@pytest.mark.parametrize(
    "payload",
    [
        {"tools": "nope"},
        {"tools": [{"description": "缺 name"}]},
        {"tools": [{"name": ""}]},
        {"tools": [{"name": "x", "inputSchema": "nope"}]},
    ],
)
async def test_session_rejects_bad_tool_schema(payload: dict) -> None:
    with pytest.raises(McpSchemaError):
        await McpSession(FakeMcpTransport([payload])).list_tools()


@pytest.mark.parametrize("payload", [{"content": "nope"}, {}])
async def test_session_rejects_bad_call_schema(payload: dict) -> None:
    with pytest.raises(McpSchemaError):
        await McpSession(FakeMcpTransport([payload])).call_tool("x", {})


async def test_session_is_error_without_text_still_has_a_readable_error() -> None:
    transport = FakeMcpTransport([{"content": [], "isError": True}])
    result = await McpSession(transport).call_tool("x", {})
    assert result.ok is False and result.error
