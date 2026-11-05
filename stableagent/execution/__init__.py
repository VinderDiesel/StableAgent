"""执行层（P5 M2）：worktree 隔离、命令执行与受限/容器两种 runner。

方向锁 integrations → execution → control → orchestration → core：本包可以判定授权
（import `control`）、读写编排事实（import `orchestration`），但**不认识集成层**——
GitHub/MCP 的对外写入归 M3/M4 的 `integrations`，由它们调用本包，不反向。
执行层版本独立于 Core 的 CONTRACT_VERSION：P6 Beta 之前不承诺接口稳定，
本包类型不在 stableagent 顶层重导出（ADR-05 决策 1）。
"""

from __future__ import annotations

EXECUTION_SCHEMA_VERSION = "0.1.0"

__all__ = ["EXECUTION_SCHEMA_VERSION"]
