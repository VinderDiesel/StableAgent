"""集成层错误：每个实例带一个稳定 code，effect handler 与 Report 按 code 映射，不靠文案。

GitHub 错误另带 `status_code` 与 `category`（可重试/不可重试/需人工三分类，规格 WP-5.5）：
状态码绝不吞掉，Report details 原样保留。分类表在 `github_client.py`，本模块只承载形状。
"""

from __future__ import annotations


class IntegrationsError(ValueError):
    """所有集成层用户可读错误的基类。

    沿用 P4/P5 口径：L1/L2 禁止为错误类型再动 Core，所以本类直接继承 `ValueError`，
    用 `.code` 提供机器可判的稳定标识。
    """

    code = "integrations.error"

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.detail = message
        self.code = code


class McpProtocolError(IntegrationsError):
    """JSON-RPC 信封不合口径：jsonrpc/id 不匹配、非 JSON、或返回了 error 成员。"""

    def __init__(self, reason: str) -> None:
        super().__init__("integrations.mcp_protocol", reason)


class McpTimeoutError(IntegrationsError):
    """请求在超时内没拿到应答；进程仍在，由调用方决定关闭还是重试。"""

    def __init__(self, method: str, timeout_s: float) -> None:
        super().__init__("integrations.mcp_timeout", f"{method} 超过 {timeout_s}s 未响应")


class McpProcessExitedError(IntegrationsError):
    """server 进程已退出（stdout EOF）：再发请求也不会有应答。"""

    def __init__(self, returncode: int | None) -> None:
        super().__init__("integrations.mcp_process_exited", f"MCP server 退出，rc={returncode}")


class McpSchemaError(IntegrationsError):
    """应答是合法 JSON-RPC，但 result 的形状不合 MCP schema。"""

    def __init__(self, method: str, reason: str) -> None:
        super().__init__("integrations.mcp_schema", f"{method} 结果不合 schema：{reason}")


class GitHubApiError(IntegrationsError):
    """GitHub 写入失败：三分类决定 effect 是退避重试还是永久失败。"""

    def __init__(self, status_code: int | None, message: str, category: str) -> None:
        super().__init__("integrations.github_api", f"{category}/{status_code}: {message}")
        self.status_code = status_code
        self.category = category
