"""可观测性（P7 M1）：领域事件、脱敏、旁路采集、进程内指标与 ticket 根 trace。

方向锁：本包可被 supervisor / orchestration / control / integrations 调用；
不得 import ``stableagent.supervisor``。
"""

from __future__ import annotations

DEFAULT_TENANT = "default"
OBSERVABILITY_SCHEMA_VERSION = "p7-observability-v1"

__all__ = ["DEFAULT_TENANT", "OBSERVABILITY_SCHEMA_VERSION"]
