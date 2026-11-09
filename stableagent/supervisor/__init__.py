"""P6 控制平面：常驻 Router、Trigger/Thread/Subscription 与只读监管视图。

方向锁：supervisor → integrations → execution → control → orchestration → core；
旧包禁止 import 本包。记录结构在 P6 Beta 前不承诺稳定（ADR-06 决策 1）。
"""

from __future__ import annotations

SUPERVISOR_SCHEMA_VERSION = "p6-supervisor-v2"

__all__ = ["SUPERVISOR_SCHEMA_VERSION"]
