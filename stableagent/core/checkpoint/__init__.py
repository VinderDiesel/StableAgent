"""P2 执行可靠性：快照存储、触发策略、版本迁移与失败自愈。

`core/loop.py` 不导入本包（P1 DoD：checkpoint 不得要求重写 loop）；
接入点在 `core/agent.py` 外层，消费 `drive()` 的 `(事件, 快照)` 对。
"""

from stableagent.core.checkpoint.manager import CheckpointManager
from stableagent.core.checkpoint.recovery import (
    MIGRATIONS,
    RecoveryDecision,
    RecoveryDirector,
    RetryPolicy,
    SnapshotVersionError,
    load_latest_state,
    migrate_state,
    pause_for_human,
    repair_for_retry,
)
from stableagent.core.checkpoint.storage import (
    CheckpointRecord,
    CheckpointStore,
    MemoryCheckpointStore,
    RedisCheckpointStore,
    SqliteCheckpointStore,
)

__all__ = [
    "MIGRATIONS",
    "CheckpointManager",
    "CheckpointRecord",
    "CheckpointStore",
    "MemoryCheckpointStore",
    "RecoveryDecision",
    "RecoveryDirector",
    "RedisCheckpointStore",
    "RetryPolicy",
    "SnapshotVersionError",
    "SqliteCheckpointStore",
    "load_latest_state",
    "migrate_state",
    "pause_for_human",
    "repair_for_retry",
]
