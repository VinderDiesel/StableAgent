"""执行层错误：每个实例带一个稳定 code，outbox handler 与 Report 按 code 映射，不靠异常文案。

被拒绝的命令默认不是异常——`CommandRunner` 把拒绝写进 `CommandResult.denied_reason`；
本家族的 `CommandDeniedError` 只用于"调用方违反了执行前提"的场合（ADR-05 决策 6/7）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


class ExecutionError(ValueError):
    """所有执行层用户可读错误的基类。

    沿用 P4/P5 口径：L1/L2 禁止为错误类型再动 Core 与编排层，所以本类直接继承
    `ValueError`，用 `.code` 提供机器可判的稳定标识。
    """

    code = "execution.error"

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.detail = message
        self.code = code


class PathEscapeError(ExecutionError):
    """resolve 后不在配置 root 内——包括借道符号链接的逃逸。"""

    def __init__(self, root: Path, candidate: str) -> None:
        super().__init__("execution.path_escape", f"路径 {candidate} 的解析结果不在 root {root} 内")


class WorktreeReuseError(ExecutionError):
    """复用判定四要素（remote / ticket / base ref / ownership）有不一致：拒绝复用并保留现场。"""

    def __init__(self, reason: str, diagnostics: dict[str, Any]) -> None:
        super().__init__("execution.worktree_reuse", f"拒绝复用 worktree：{reason}")
        self.diagnostics = diagnostics


class CommandDeniedError(ExecutionError):
    """调用方要求的执行前提不成立（如未定义的 network profile）；普通命令拒绝走结果字段。"""

    def __init__(self, reason: str, argv: tuple[str, ...]) -> None:
        super().__init__("execution.command_denied", f"拒绝执行 {list(argv)}：{reason}")


class CleanupNotConfirmedError(ExecutionError):
    """清理必须带人工确认参数：Ticket 结束后 worktree 默认保留供审查。"""

    def __init__(self, path: Path) -> None:
        super().__init__(
            "execution.cleanup_unconfirmed",
            f"未确认清理 {path}：现场默认保留供审查，确认删除请调用 "
            f"manager.cleanup(handle, confirm=True)（连分支一起删再加 delete_branch=True）",
        )
