"""git 传输层（ADR-05 决策 6，M2 取向 ①）：只以参数数组调用 `git`，永不经 shell。

`Git` 协议让 worktree 管理不绑定具体进程实现；`SubprocessGit` 是生产实现：argv 首元固定为
`git`、`shell=False`、必有超时与输出上限、子进程环境只有四项且不含任何宿主凭据（L7）。
调用失败抛 `GitError`——它是操作失败而不是策略拒绝，所以不进 `ExecutionError` 家族（那四个
稳定码归 Task 1 的口径），带 returncode 与截断输出，交给 handler 与 Report 归类。

输出与错误文本一律经 `scrub_credentials`：git 在鉴权失败时会把含凭据的 URL 打进 stderr，
本层不留给上层"忘了脱敏"的机会。
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

GIT_EXECUTABLE = "git"
DEFAULT_TIMEOUT_S = 30.0
DEFAULT_MAX_OUTPUT_CHARS = 200_000

# URL 的 userinfo 段：`scheme://user:pass@host` —— 整段剥掉，不保留掩码形态（幂等、可比对）。
_USERINFO = re.compile(r"://[^/\s@]*@")

_ERROR_CLIP_CHARS = 400


def scrub_credentials(text: str) -> str:
    """剥掉 URL userinfo；这是 remote 身份的比较口径，也是任何出层文本的脱敏口径。"""
    return _USERINFO.sub("://", text)


def git_env(*, path: str | None = None) -> dict[str, str]:
    """git 子进程的最小环境：只有这四项，宿主变量一律不带（L7）。

    `GIT_TERMINAL_PROMPT=0` 保证凭据缺失时直接失败而不是挂在 tty 上；
    `GIT_CONFIG_NOSYSTEM=1` 与不注入 `HOME` 一起，杜绝系统/用户配置把凭据助手拉进来。
    """
    return {
        "PATH": path if path is not None else os.environ.get("PATH", ""),
        "LANG": "C",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
    }


@dataclass(frozen=True)
class GitResult:
    returncode: int
    stdout: str
    stderr: str


class GitError(RuntimeError):
    """git 返回非 0 或超时：带 returncode（超时为 None）与已脱敏的输出。"""

    def __init__(
        self, argv: Sequence[str], returncode: int | None, stdout: str, stderr: str
    ) -> None:
        self.argv = tuple(argv)
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        tail = scrub_credentials(stderr)[:_ERROR_CLIP_CHARS]
        super().__init__(
            f"git 调用失败 argv={list(self.argv)} returncode={returncode} stderr={tail!r}"
        )


class Git(Protocol):
    def run(self, args: Sequence[str], *, cwd: Path) -> GitResult: ...


class SubprocessGit:
    """`Git` 的真实实现：`subprocess.run` + 参数数组，不解析任何 shell 语法。"""

    def __init__(
        self,
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
    ) -> None:
        self._timeout_s = timeout_s
        self._max_output_chars = max_output_chars

    def run(self, args: Sequence[str], *, cwd: Path) -> GitResult:
        argv = [GIT_EXECUTABLE, *(str(arg) for arg in args)]
        try:
            done = subprocess.run(
                argv,
                cwd=cwd,
                env=git_env(),
                shell=False,
                capture_output=True,
                text=True,
                timeout=self._timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise GitError(argv, None, "", f"git 在 {self._timeout_s}s 内未返回") from exc
        stdout = scrub_credentials(done.stdout or "")[: self._max_output_chars]
        stderr = scrub_credentials(done.stderr or "")[: self._max_output_chars]
        if done.returncode != 0:
            raise GitError(argv, done.returncode, stdout, stderr)
        return GitResult(returncode=done.returncode, stdout=stdout, stderr=stderr)
