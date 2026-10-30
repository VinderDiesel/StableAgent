"""恢复侧（P2 交付：`recovery.py`）：快照版本迁移 + 退避重试 + 失败态修复。

三块职责都是纯函数/纯决策，便于单测（DoD C/E 节）：
- `migrate_state`：把任意已知旧版本的快照升到当前 `CONTRACT_VERSION`；
- `RetryPolicy`：指数退避序列（`sleep` 由调用方注入，测试不真等）；
- `RecoveryDirector`：读错误分类，决定 重试 / 转人工暂停 / 认失败。
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Literal

from pydantic import BaseModel

from stableagent.core.checkpoint.storage import CheckpointStore
from stableagent.core.errors import ErrorClass, classify_error_detail
from stableagent.core.state import (
    CONTRACT_VERSION,
    AgentState,
    AgentStatus,
    CheckpointConfig,
)

__all__ = [
    "MIGRATIONS",
    "RecoveryDecision",
    "RecoveryDirector",
    "RetryPolicy",
    "SnapshotVersionError",
    "Sleeper",
    "load_latest_state",
    "migrate_payload",
    "migrate_state",
    "pause_for_human",
    "repair_for_retry",
    "register_migration",
]

# 最早可识别的快照版本（缺 contract_version 字段的老快照按此处理）
EARLIEST_KNOWN_VERSION = "1.0.0"


class SnapshotVersionError(ValueError):
    """快照版本无法迁移：未知版本或来自更高版本。宁可报错，绝不猜测语义。"""


Migration = Callable[[dict[str, Any]], dict[str, Any]]
# key = 起始版本，value = (目标版本, 升级函数)
MIGRATIONS: dict[str, tuple[str, Migration]] = {}


def register_migration(source: str) -> Callable[[Migration], Migration]:
    def decorator(func: Migration) -> Migration:
        MIGRATIONS[source] = (_next_version(source), func)
        return func

    return decorator


def _next_version(source: str) -> str:
    """按语义化版本规则推下一版本：v1.0.0 -> v1.1.0（兼容扩展只抬 minor）。"""
    parts = [int(x) for x in source.split(".")]
    return f"{parts[0]}.{parts[1] + 1}.0"


def _as_tuple(version: str) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in version.split("."))
    except ValueError as exc:
        raise SnapshotVersionError(f"无法解析契约版本：{version!r}") from exc


def _payload_of(payload: str | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(payload, Mapping):
        return dict(payload)
    return json.loads(payload)


def migrate_payload(payload: str | Mapping[str, Any]) -> dict[str, Any]:
    data = _payload_of(payload)
    version = str(data.get("contract_version") or EARLIEST_KNOWN_VERSION)
    target = _as_tuple(CONTRACT_VERSION)
    steps = 0
    while _as_tuple(version) != target:
        if _as_tuple(version) > target:
            raise SnapshotVersionError(
                f"快照版本 {version} 高于当前契约 {CONTRACT_VERSION}，请升级框架"
            )
        if version not in MIGRATIONS or steps > len(MIGRATIONS):
            raise SnapshotVersionError(f"快照版本 {version} 无迁移路径到 {CONTRACT_VERSION}")
        version, func = MIGRATIONS[version]
        data = func(data)
        steps += 1
    data["contract_version"] = CONTRACT_VERSION
    return data


def migrate_state(payload: str | Mapping[str, Any]) -> AgentState:
    """旧快照 -> 当前版本 `AgentState`（读取侧唯一入口，DoD E4）。"""
    return AgentState.model_validate(migrate_payload(payload))


async def load_latest_state(store: CheckpointStore, run_id: str) -> AgentState | None:
    record = await store.load(run_id)
    if record is None:
        return None
    return migrate_state(record.payload)


def _backfill_v1_1_0(data: dict[str, Any]) -> dict[str, Any]:
    """v1.0.0 -> v1.1.0：仅补齐新增可选字段的默认值（非破坏性）。"""
    upgraded = dict(data)
    upgraded.setdefault("usage", {})
    messages = []
    for message in upgraded.get("messages") or []:
        item = dict(message)
        item.setdefault("tool_calls", [])
        messages.append(item)
    upgraded["messages"] = messages
    return upgraded


register_migration("1.0.0")(_backfill_v1_1_0)


class RetryPolicy(BaseModel):
    """指数退避：第 n 次重试等待 `min(base * factor**(n-1), max_delay)`。"""

    max_attempts: int = 3
    base_delay_seconds: float = 1.0
    backoff_factor: float = 2.0
    max_delay_seconds: float = 30.0

    @classmethod
    def from_config(cls, config: CheckpointConfig) -> RetryPolicy:
        return cls(
            max_attempts=config.retry_max_attempts,
            base_delay_seconds=config.retry_base_delay_seconds,
            backoff_factor=config.retry_backoff_factor,
            max_delay_seconds=config.retry_max_delay_seconds,
        )

    def delay_for(self, attempt: int) -> float:
        """attempt 从 1 开始（第 1 次重试的等待）。"""
        if attempt < 1:
            raise ValueError("attempt 从 1 起算")
        raw = self.base_delay_seconds * (self.backoff_factor ** (attempt - 1))
        return min(raw, self.max_delay_seconds)

    def delays(self) -> list[float]:
        return [self.delay_for(n) for n in range(1, max(self.max_attempts, 1))]


def repair_for_retry(state: AgentState) -> AgentState:
    """失败态里剥掉失败标记：messages/step 不动，已完成步不重跑（DoD C1）。"""
    return state.model_copy(update={"status": AgentStatus.RUNNING, "error": None})


def pause_for_human(state: AgentState) -> AgentState:
    """需人工的失败停在 PAUSED，保留 error 文本供人判读，处理后 resume 复用 F3 语义。"""
    return state.model_copy(update={"status": AgentStatus.PAUSED})


class RecoveryDecision(BaseModel):
    """一次失败的处理决定。"""

    action: Literal["fail", "retry", "needs_human"]
    error_class: ErrorClass
    attempt: int = 0
    delay_seconds: float = 0.0
    provider_index: int = 0
    detail: str = ""


class RecoveryDirector:
    """把"错误分类 + 已试次数 + 可用 provider 数"折算成下一步动作。"""

    def __init__(
        self,
        *,
        strategy: str,
        policy: RetryPolicy | None = None,
        provider_count: int = 1,
    ) -> None:
        self._strategy = strategy
        self._policy = policy or RetryPolicy()
        self._provider_count = max(provider_count, 1)

    def decide(self, detail: str, *, attempts: int = 0) -> RecoveryDecision:
        """attempts = 已发生的调用次数（首次运行为 0）。
        max_attempts 含首次调用，故可重试次数为 max_attempts - 1。"""
        error_class = classify_error_detail(detail)
        if self._strategy != "none" and error_class is ErrorClass.NEEDS_HUMAN:
            return RecoveryDecision(action="needs_human", error_class=error_class, detail=detail)
        retries_left = self._policy.max_attempts - 1
        if (
            self._strategy == "retry_with_fallback"
            and error_class is ErrorClass.RETRYABLE
            and attempts <= retries_left - 1
        ):
            retry_no = attempts + 1
            return RecoveryDecision(
                action="retry",
                error_class=error_class,
                attempt=retry_no,
                delay_seconds=self._policy.delay_for(retry_no),
                provider_index=retry_no % self._provider_count,
                detail=detail,
            )
        return RecoveryDecision(action="fail", error_class=error_class, detail=detail)


Sleeper = Callable[[float], Awaitable[None]]
