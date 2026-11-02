"""StableAgent：面向生产环境的轻量级 Agent 运行时框架。

P0 交付公共契约与可构造的 Agent 骨架，P1 交付执行引擎，P2 交付执行可靠性
（checkpoint / 恢复 / 失败自愈）。公共契约的版本与不变式见
docs/design-decisions/00-contracts.md。
"""

from stableagent.core.agent import Agent
from stableagent.core.checkpoint.manager import CheckpointManager
from stableagent.core.checkpoint.recovery import (
    RetryPolicy,
    SnapshotVersionError,
    migrate_state,
)
from stableagent.core.checkpoint.storage import (
    CheckpointRecord,
    CheckpointStore,
    MemoryCheckpointStore,
    RedisCheckpointStore,
    SqliteCheckpointStore,
)
from stableagent.core.errors import ErrorClass, classify_error, classify_error_detail
from stableagent.core.loop import AgentLoop
from stableagent.core.state import (
    CONTRACT_VERSION,
    AgentState,
    AgentStatus,
    Config,
    Event,
    EventType,
    Message,
    ToolCall,
)
from stableagent.providers.base import Provider, ProviderChunk, ProviderResponse
from stableagent.providers.openai import OpenAIProvider
from stableagent.tools.evidence import Evidence
from stableagent.tools.registry import Tool, ToolRegistry, ToolResult

__version__ = "0.1.0"

__all__ = [
    "CONTRACT_VERSION",
    "Agent",
    "AgentLoop",
    "AgentState",
    "AgentStatus",
    "CheckpointManager",
    "CheckpointRecord",
    "CheckpointStore",
    "Config",
    "ErrorClass",
    "Event",
    "EventType",
    "Evidence",
    "MemoryCheckpointStore",
    "Message",
    "OpenAIProvider",
    "Provider",
    "ProviderChunk",
    "ProviderResponse",
    "RedisCheckpointStore",
    "RetryPolicy",
    "SnapshotVersionError",
    "SqliteCheckpointStore",
    "Tool",
    "ToolCall",
    "ToolRegistry",
    "ToolResult",
    "__version__",
    "classify_error",
    "classify_error_detail",
    "migrate_state",
]
