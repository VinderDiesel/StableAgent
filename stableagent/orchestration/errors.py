"""编排层错误：每个实例带一个稳定 code，P5/P6 按 code 映射，不靠异常类型文案。"""

from __future__ import annotations


class OrchestrationError(ValueError):
    """所有编排层用户可读错误的基类。

    Core 里没有 `UserError` 基类（规格第 6 节第 8 条记录这一口径）：L2 禁止为错误类型再动 Core，
    所以本类直接继承 `ValueError`，用 `.code` 提供机器可判的稳定标识。
    """

    code = "orchestration.error"

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.detail = message
        self.code = code


class IllegalTransitionError(OrchestrationError):
    def __init__(self, machine: str, current: str, action: str) -> None:
        super().__init__(
            "orchestration.illegal_transition",
            f"{machine} 不允许从 {current} 执行 {action}",
        )


class UnknownTicketError(OrchestrationError):
    def __init__(self, ticket_id: str) -> None:
        super().__init__("orchestration.unknown_ticket", f"没有 ticket_id={ticket_id} 的记录")


class UnknownAssignmentError(OrchestrationError):
    def __init__(self, assignment_id: str) -> None:
        super().__init__(
            "orchestration.unknown_assignment", f"没有 assignment_id={assignment_id} 的记录"
        )


class ReentrantTransactionError(OrchestrationError):
    def __init__(self) -> None:
        super().__init__(
            "orchestration.reentrant_transaction",
            "Store.transaction() 不可重入：事务体内禁止再次开启事务或 await",
        )


class DuplicateLaunchError(OrchestrationError):
    def __init__(self, worker_run_id: str) -> None:
        super().__init__(
            "orchestration.duplicate_launch",
            f"worker_run_id={worker_run_id} 已有驱动者，拒绝第二个（L7 单 run 单驱动）",
        )


class WorkerConfigError(OrchestrationError):
    def __init__(self, reason: str) -> None:
        super().__init__("orchestration.worker_config", f"Worker 配置不可编排：{reason}")


class IdempotencyConflictError(OrchestrationError):
    def __init__(self, kind: str, key: str) -> None:
        super().__init__("orchestration.idempotency_conflict", f"effect {kind}/{key} 已处于终态")


class DeferrableEffectError(OrchestrationError):
    """此刻不可执行而非失败：dispatcher 回退时还掉 claim 的那次 attempts，不侵蚀重试预算。"""

    def __init__(self, reason: str) -> None:
        super().__init__("orchestration.effect_deferred", f"effect 此刻顺延，不是失败：{reason}")


class CyclicDependencyError(OrchestrationError):
    def __init__(self, ticket_id: str, path: list[str]) -> None:
        super().__init__(
            "orchestration.cyclic_dependency",
            f"为 {ticket_id} 添加依赖将成环：{' -> '.join(path)}",
        )
