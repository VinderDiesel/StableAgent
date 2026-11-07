"""集成层（P5 M3）：MCP 工具适配与 GitHub 显式请求闭环。

方向锁 integrations → execution → control → orchestration → core：本包是最上游，
可以调用执行层、判定授权、读写编排事实，但没有任何旧包认识它（L2）。
`mcp.py` 另有一条更窄的口径：它只产出既有 `Tool`，不 import control/execution——
调用前的 revalidate 与审计由 `control.tool_filter` 包在 Tool 外层完成（ADR-05 决策 8）。
集成层版本独立于 Core 的 CONTRACT_VERSION：P6 Beta 之前不承诺接口稳定，
本包类型不在 stableagent 顶层重导出（ADR-05 决策 1）。
"""

from __future__ import annotations

INTEGRATIONS_SCHEMA_VERSION = "0.1.0"

__all__ = ["INTEGRATIONS_SCHEMA_VERSION"]
