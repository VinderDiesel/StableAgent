"""StableAgent：面向生产环境的轻量级 Agent 运行时框架。

Phase 0 仅交付公共契约与可构造的 Agent 骨架；执行引擎在 Phase 1 落地。
公共契约的版本与不变式见 docs/design-decisions/00-contracts.md。
"""

from stableagent.core.agent import Agent
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
from stableagent.tools.registry import Evidence, Tool, ToolRegistry, ToolResult

__version__ = "0.1.0"

__all__ = [
    "CONTRACT_VERSION",
    "Agent",
    "AgentLoop",
    "AgentState",
    "AgentStatus",
    "Config",
    "Event",
    "EventType",
    "Message",
    "Provider",
    "ProviderChunk",
    "ProviderResponse",
    "OpenAIProvider",
    "Evidence",
    "Tool",
    "ToolCall",
    "ToolRegistry",
    "ToolResult",
    "__version__",
]
