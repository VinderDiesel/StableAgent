"""最小 MCP stdio server（测试 fixture）：换行分帧、只用标准库、不联网。

故意把 `secret` 列进 tools/list 并让 tools/call 照常回显——E3 要证明的是
"server 愿意提供"与"Agent 被授权调用"是两件事，拒绝必须发生在我们这一侧。
"""

from __future__ import annotations

import json
import sys

TOOLS = [
    {
        "name": "echo",
        "description": "回显 arguments",
        "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}},
    },
    {
        "name": "secret",
        "description": "fixture 用：server 侧不设防，授权由客户端裁决",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _reply(request_id: object, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def handle(request: dict) -> dict | None:
    method = request.get("method")
    if method == "initialize":
        return _reply(
            request["id"],
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "echo-fixture", "version": "0.1.0"},
            },
        )
    if method == "tools/list":
        return _reply(request["id"], {"tools": TOOLS})
    if method == "tools/call":
        arguments = (request.get("params") or {}).get("arguments") or {}
        text = json.dumps(arguments, sort_keys=True, ensure_ascii=False)
        return _reply(request["id"], {"content": [{"type": "text", "text": text}]})
    if "id" not in request:
        return None
    return {
        "jsonrpc": "2.0",
        "id": request["id"],
        "error": {"code": -32601, "message": f"unknown method: {method}"},
    }


def main() -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        reply = handle(json.loads(line))
        if reply is None:
            continue
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
