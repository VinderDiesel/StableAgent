"""控制平面（P5 M1）：结构化授权判定、审批流转与授权审计。

授权事实源在 `orchestration.Store`（同一 SQLite 文件、同一 `transaction()` 入口），
判定逻辑在本包：`control` 不自建存储库，也不 import 执行层与集成层。
控制层版本独立于 Core 的 CONTRACT_VERSION：P6 Beta 之前不承诺记录结构稳定，
因此本包类型不在 stableagent 顶层重导出（决策见 ADR-05 决策 1）。
"""

from __future__ import annotations

CONTROL_SCHEMA_VERSION = "0.1.0"

__all__ = ["CONTROL_SCHEMA_VERSION"]
