"""核心契约：Event / Message / AgentState / Config（契约 v1.1.0，P1 修订见 CHANGELOG）。

版本与不变式规范见 docs/design-decisions/00-contracts.md。
P2 起本模块字段只扩展不推翻；扩展形态 = 新增带默认值的可选字段 / 新增枚举成员。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

CONTRACT_VERSION = "1.1.0"


class EventType(str, Enum):
    """统一事件流类型。新增成员属于兼容性扩展（P1 新增 paused，P2 新增 retry）。"""

    THINKING = "thinking"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    CHECKPOINT = "checkpoint"
    PAUSED = "paused"
    RETRY = "retry"
    ERROR = "error"
    DONE = "done"


TERMINAL_EVENTS = frozenset({EventType.DONE, EventType.ERROR})


class Event(BaseModel):
    """Agent 对外输出的唯一通信单元；发布后不可变（frozen）。"""

    model_config = ConfigDict(frozen=True)

    type: EventType
    data: dict[str, Any] = Field(default_factory=dict)
    step: int = 0


class ToolCall(BaseModel):
    """模型发起的一次工具调用请求。

    v1.1.0 起定义于本模块（Message.tool_calls 需要引用），
    `stableagent.providers.base` 保留重导出，旧导入路径不变。
    """

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class Message(BaseModel):
    """对话消息。工具结果通过 role='tool' + tool_call_id 关联回 ToolCall。"""

    role: Literal["system", "user", "assistant", "tool"]
    content: str
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)


class AgentStatus(str, Enum):
    """运行状态机：PENDING -> RUNNING -> (PAUSED <-> RUNNING) -> SUCCEEDED | FAILED。"""

    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class AgentState(BaseModel):
    """一次运行的可序列化快照，是 loop 的唯一事实源（状态外置契约）。

    不变式：执行引擎不携带隐式内存态，一切可恢复信息都在本模型内；
    P2 的 checkpoint 即对本模型做持久化，不新增私有状态。
    """

    contract_version: str = CONTRACT_VERSION
    run_id: str
    agent_name: str
    status: AgentStatus = AgentStatus.PENDING
    step: int = 0
    messages: list[Message] = Field(default_factory=list)
    context_summary: str = ""
    """P3 扩展：已折叠历史段的摘要。带默认值，旧快照缺字段即取空串（非破坏性）。"""
    usage: dict[str, int] = Field(default_factory=dict)
    error: str | None = None

    def snapshot(self) -> str:
        return self.model_dump_json()

    @classmethod
    def loads(cls, data: str) -> AgentState:
        return cls.model_validate_json(data)


class ProviderConfig(BaseModel):
    model: str = "gpt-4o"
    api_base: str | None = None
    request_timeout_seconds: float = 60.0


class ContextConfig(BaseModel):
    assembly: str = "passthrough"
    compression: str = "none"
    capability_filter: str = "static"
    max_context_tokens: int | None = None
    on_budget_exceeded: Literal["compress", "degrade", "reject"] = "compress"
    # P3 扩展（全部带默认值；compression="none" 时不生效，默认路径行为不变）
    keep_recent_messages: int = 8
    max_summary_chars: int = 1200
    tool_result_excerpt_chars: int = 200
    max_visible_tools: int = 8
    """能力视图上限（C 节）；常驻集合（本次已用过的工具）优先于此上限，见 C3。"""


class CheckpointConfig(BaseModel):
    """P2 消费的配置节。P2 新增字段全部带默认值（兼容性扩展）。"""

    backend: Literal["memory", "sqlite", "redis"] = "memory"
    auto_save_every: int = 0
    recovery_strategy: Literal["none", "resume", "retry_with_fallback"] = "none"
    # 存储后端参数：sqlite 需要路径；redis 客户端由外部注入，本框架不硬依赖
    sqlite_path: str | None = None
    redis_key_prefix: str = "stableagent:ckpt"
    history_limit: int = 20
    # 失败自愈：指数退避（recovery_strategy=retry_with_fallback 时生效）
    retry_max_attempts: int = 3
    retry_base_delay_seconds: float = 1.0
    retry_backoff_factor: float = 2.0
    retry_max_delay_seconds: float = 30.0


class SandboxConfig(BaseModel):
    backend: Literal["none", "docker"] = "none"
    timeout_seconds: float = 60.0
    network: Literal["none", "allowlist", "full"] = "none"


class MemoryConfig(BaseModel):
    short_term_enabled: bool = True
    long_term_backend: str = "none"


class ControlConfig(BaseModel):
    max_total_tokens: int | None = None
    permission_hook: str = "allow_all"
    require_approval_tools: list[str] = Field(default_factory=list)
    max_steps: int = 10


class Config(BaseModel):
    """组合式配置：Agent = Provider + ContextEngine + Checkpoint + Sandbox + Memory + Control。

    扩展形态 = 新增带默认值的配置节；新增配置节属于兼容性扩展。
    """

    provider: ProviderConfig = Field(default_factory=ProviderConfig)
    context: ContextConfig = Field(default_factory=ContextConfig)
    checkpoint: CheckpointConfig = Field(default_factory=CheckpointConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    control: ControlConfig = Field(default_factory=ControlConfig)
