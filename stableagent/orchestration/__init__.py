"""P4 控制平面：Ticket / Assignment / Report / Effect 与调度。

编排层版本独立于 Core 的 CONTRACT_VERSION：P6 Beta 之前不承诺记录结构稳定，
因此本包类型不在 stableagent 顶层重导出（决策见 ADR-04 决策 1）。
"""

from __future__ import annotations

ORCHESTRATION_SCHEMA_VERSION = "0.1.0"

__all__ = ["ORCHESTRATION_SCHEMA_VERSION"]
