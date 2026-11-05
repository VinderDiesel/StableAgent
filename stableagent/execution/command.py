"""命令边界：argv 数组 + 精确 allowlist + cwd 收口 + 超时 + 输出上限与脱敏 + shell 语法拒绝
（规格 2.4，C7、C6）。

结构性约束先说清楚：`run()` 只有 `request` 一个形参，**没有 `shell` 开关**，也不接受命令字符串——
`shell=True` 在这里不是"被关掉"，是写不出来。

拒绝**不抛异常**：规格 2.4 要求被拒原因待在 `CommandResult` 里，且拒绝发生在 spawn 之前，所以
被拒的命令没有任何副作用。每一次拒绝都过一遍可注入的 `audit(request, reason)` 回调（默认 no-op，
M3 接 Report/审计）。拒绝原因是稳定码，不是文案（映射由调用方按码做）：

| 码 | 触发 | 顺序 |
|---|---|---|
| `cwd_escape` | `confine(worktree_root, request.cwd)` 抛 `PathEscapeError` | ① |
| `empty_argv` | `argv` 为空（没有可判定的命令名） | ② |
| `command_not_allowed` | `argv[0]` 不在 allowlist 里 | ③ |
| `shell_syntax` | 任一 argv 元素含 `SHELL_METACHARACTERS` 之一 | ④ |

两条口径写在名字旁边：

* **allowlist 逐字节精确匹配 `argv[0]`**：不取 basename、不做 `PATH` 归一化。名单里写 `pytest` 就
  只放行 `pytest`，传 `/usr/bin/pytest` 或反向都拒——归一化等于替调用方猜意图，而猜错的方向是放行。
  附带一条 exec 语义：裸名由 `execvp` 按**父进程**的 `PATH` 解析（子进程环境里那份 `PATH` 只影响
  它自己以后再 spawn 的进程）；名单里的名字在 `PATH` 上不存在时 `FileNotFoundError` 原样抛出——
  那是部署配置错误，不是策略拒绝，吞掉它只会让"为什么什么都没跑"无处可查。
* **`max_output_bytes` 是含尾标的总预算**：截断后 `stdout`/`stderr` 各自的 UTF-8 字节数不超过它，
  尾标 `...[truncated N bytes]` 里的 N 是被丢掉的字节数。先按 `redact` 逐个替换成 `***`、再截断，
  顺序反过来会让落在切口前的密钥原样进 Report。

④ 为什么在 `shell=False` 之下还要拦 shell 语法：本边界管的是**请求**，不是这一档实现怎么 spawn。
`CommandRequest` 会流过不同的 runner（`DockerRunner` 里换成形如 `sh -c` 的包装、集成层里可能拼命令
串），把语法字符挡在请求里，"这个请求在任何实现下都不会被 shell 解释"才是可证的性质，而不依赖
"当前这个实现恰好没起 shell"。代价说清楚：这是字符表策略，必然误伤正文（`US$5`、带 `;` 的代码片段
都会被拒），方向是**拒而不是放**，所以错的只会是可用性；要放行文本类参数得换 policy，不在这里开口。
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from stableagent.execution.environment import build_child_env
from stableagent.execution.errors import PathEscapeError
from stableagent.execution.paths import confine
from stableagent.orchestration.model import Clock

#: 尾标的模板；`{dropped}` 的位数参与预算计算，见 `_fit`。
TRUNCATION_MARKER = "...[truncated {dropped} bytes]"
REDACTED = "***"

#: 本地受限执行的模式名。叙事纪律（ADR-05 决策 7）：这一档统一写 `local-restricted`，
#: 换实现就得换字符串——`DockerRunner` 不得沿用默认值冒充本地执行。
LOCAL_RESTRICTED = "local-restricted"

#: shell 语法字符（规格 2.4 / C6）：`;` `&` `|` `$` 反引号、换行、回车。
#: 判的是"这个请求有没有可能被 shell 解释"，不是"当前实现有没有起 shell"——见模块文档 ④。
SHELL_METACHARACTERS = frozenset(";&|$`\n\r")


def _no_audit(request: CommandRequest, reason: str) -> None:
    """拒绝审计的默认回调：什么都不做。M3 把它接到 Report/审计写入，本轮不预设目的地。"""


def deny_reason(
    request: CommandRequest, *, allowlist: frozenset[str], worktree_root: Path
) -> str | None:
    """四条判定的唯一实现（①cwd ②空 argv ③allowlist ④shell 语法），放行返回 `None`。

    顺序即优先级，一次请求只报第一条。提到模块函数而不是留在 `CommandRunner` 里，是为了让
    `DockerRunner` 共用同一份规则：容器档若带一份自己的复制品，两边迟早漂出"本地拒、容器放"的缝。
    `confine` 在这里与 `run()` 取路径时各调一次——同一个收口实现（C4），两次结果必然同形。
    """
    try:
        confine(worktree_root, request.cwd)
    except PathEscapeError:
        return "cwd_escape"
    if not request.argv:
        return "empty_argv"
    if request.argv[0] not in allowlist:
        return "command_not_allowed"
    if any(char in SHELL_METACHARACTERS for arg in request.argv for char in arg):
        return "shell_syntax"
    return None


def denied_result(
    request: CommandRequest,
    reason: str,
    *,
    clock: Clock,
    mode: str = LOCAL_RESTRICTED,
    audit: Callable[[CommandRequest, str], None] = _no_audit,
) -> CommandResult:
    """拒绝的唯一出口：一次时钟读数 + 一次审计回调 + 一份"没有副作用"的结果。

    `started_at == ended_at` 就是没 spawn 的证据（判定发生在 spawn 之前）。回调抛出的异常原样
    传出——拒绝已经决定，记录失败不能把结论改成放行。
    """
    now = clock.now_ms()
    audit(request, reason)
    return CommandResult(
        argv=request.argv,
        exit_code=None,
        stdout="",
        stderr="",
        started_at=now,
        ended_at=now,
        truncated=False,
        timed_out=False,
        denied_reason=reason,
        mode=mode,
    )


def fit_output(
    text: str, *, max_output_bytes: int, secrets: tuple[str, ...] = ()
) -> tuple[str, bool]:
    """先脱敏后截断，返回（不超过预算的文本, 是否被截）。

    `max_output_bytes` 是含尾标的总预算：尾标 `...[truncated N bytes]` 的位数随 dropped 增长，
    所以按 `len(data)`（dropped 的上界）那一份形态预留预算，真实尾标只会更短，"正文 + 尾标 ≤ 预算"
    恒成立。顺序不能换——先截后脱敏会让落在切口前的密钥原样进 Report。
    """
    redacted = text
    for secret in secrets:
        if secret:  # 空串会让 `replace` 在每个字符间插星号：那是自伤，不是脱敏
            redacted = redacted.replace(secret, REDACTED)
    data = redacted.encode("utf-8")
    if len(data) <= max_output_bytes:
        return redacted, False
    reserve = TRUNCATION_MARKER.format(dropped=len(data)).encode("utf-8")
    keep = max(max_output_bytes - len(reserve), 0)
    body = data[:keep].decode("utf-8", "ignore")  # 切口落在半个字符上就丢掉那半个
    dropped = len(data) - len(body.encode("utf-8"))
    return body + TRUNCATION_MARKER.format(dropped=dropped), True


@dataclass(frozen=True)
class CommandContext:
    """一次执行属于哪个 Ticket/Assignment——`build_child_env` 需要的四个标识。

    放在 request 而不是 runner 上：`worktree_root` 是全部 Ticket 共用的根，一个 runner 服务多个
    Ticket，所以身份只能跟着请求走。`worktree` 不在这里——它是 `run()` 收口后的 cwd，由 runner
    填，调用方没有"报一个别的目录"的余地。
    """

    ticket_id: str
    assignment_id: str
    repository: str
    base_ref: str


@dataclass(frozen=True)
class CommandRequest:
    """`argv` 是参数数组（首元是命令名），`cwd` 可以是 root 内的相对形式；
    `timeout_s=None` 走 runner 的默认超时。
    """

    argv: tuple[str, ...]
    cwd: Path
    context: CommandContext
    timeout_s: float | None = None


@dataclass(frozen=True)
class CommandResult:
    """一次执行（或一次拒绝）的全部事实：拒绝时 `exit_code is None` 且两路输出为空。

    `mode` 由生产者写明自己是谁：`CommandRunner` 是 `local-restricted`，容器实现必须换成自己的
    名字。默认值是本地执行那一个，因为默认生产者就是本地的 `CommandRunner`。
    """

    argv: tuple[str, ...]
    exit_code: int | None
    stdout: str
    stderr: str
    started_at: int
    ended_at: int
    truncated: bool
    timed_out: bool
    denied_reason: str | None
    mode: str = LOCAL_RESTRICTED

    def summary(self) -> str:
        """进 Report 的一行事实：模式、结果码、是否截断、耗时。

        不含 `stdout`/`stderr` 的任何原文——正文里可能有密钥，也可能根本不是我们的文本，
        而 Report 是长期留存的那一份。要正文就读 `CommandResult` 本身。
        结果码三分：被拒写稳定码（`denied=...`），超时没有退出码（`timed_out`），其余写
        `exit=N`。模式串只来自 `mode`，本方法不推断也不美化它。
        """
        if self.denied_reason is not None:
            outcome = f"denied={self.denied_reason}"
        elif self.timed_out:
            outcome = "timed_out"
        else:
            outcome = f"exit={self.exit_code}"
        return (
            f"{self.mode} {outcome} "
            f"output={'truncated' if self.truncated else 'intact'} "
            f"duration_ms={self.ended_at - self.started_at}"
        )


class CommandRunner:
    """按 allowlist 放行的本地命令执行器：模式是 `local-restricted`，它不是安全边界——
    包装与叙事纪律见 `LocalRestrictedRunner`。"""

    def __init__(
        self,
        allowlist: frozenset[str],
        *,
        worktree_root: Path,
        clock: Clock,
        max_output_bytes: int = 64 * 1024,
        timeout_s: float = 60.0,
        redact: tuple[str, ...] = (),
        audit: Callable[[CommandRequest, str], None] = _no_audit,
    ) -> None:
        self._allowlist = frozenset(allowlist)
        self._worktree_root = Path(worktree_root)
        self._clock = clock
        self._max_output_bytes = max_output_bytes
        self._timeout_s = timeout_s
        self._secrets = tuple(redact)
        self._audit = audit

    def run(self, request: CommandRequest) -> CommandResult:
        """判定 → spawn → 归一化输出；四条判定按 ①cwd ②空 argv ③allowlist ④shell 语法短路。"""
        reason = self._deny_reason(request)
        if reason is not None:
            return self._denied(request, reason)
        worktree = confine(self._worktree_root, request.cwd)
        env = build_child_env(
            ticket_id=request.context.ticket_id,
            assignment_id=request.context.assignment_id,
            worktree=worktree,
            repository=request.context.repository,
            base_ref=request.context.base_ref,
        )
        started = self._clock.now_ms()
        try:
            done = subprocess.run(
                list(request.argv),
                cwd=worktree,
                env=env,
                timeout=request.timeout_s if request.timeout_s is not None else self._timeout_s,
                shell=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            # 超时既不是成功也不是拒绝：没有退出码，但已经产出的部分要留着看。
            stdout, first_cut = self._fit(stream_text(exc.stdout))
            stderr, second_cut = self._fit(stream_text(exc.stderr))
            return CommandResult(
                argv=request.argv,
                exit_code=None,
                stdout=stdout,
                stderr=stderr,
                started_at=started,
                ended_at=self._clock.now_ms(),
                truncated=first_cut or second_cut,
                timed_out=True,
                denied_reason=None,
            )
        stdout, first_cut = self._fit(done.stdout)
        stderr, second_cut = self._fit(done.stderr)
        return CommandResult(
            argv=request.argv,
            exit_code=done.returncode,
            stdout=stdout,
            stderr=stderr,
            started_at=started,
            ended_at=self._clock.now_ms(),
            truncated=first_cut or second_cut,
            timed_out=False,
            denied_reason=None,
        )

    def _deny_reason(self, request: CommandRequest) -> str | None:
        """委托给模块级 `deny_reason`：四条判定的实现只有一份，`DockerRunner` 用的是同一个。"""
        return deny_reason(request, allowlist=self._allowlist, worktree_root=self._worktree_root)

    def _denied(self, request: CommandRequest, reason: str) -> CommandResult:
        """委托给模块级 `denied_result`：拒绝出口同样只有一份，模式取本档的名字。"""
        return denied_result(request, reason, clock=self._clock, audit=self._audit)

    def _fit(self, text: str) -> tuple[str, bool]:
        """委托给模块级 `fit_output`：先脱敏后截断的顺序由那一个实现守着。"""
        return fit_output(text, max_output_bytes=self._max_output_bytes, secrets=self._secrets)


def stream_text(value: bytes | str | None) -> str:
    """`TimeoutExpired.stdout` 在 text 模式下是 str，字节模式下是 bytes——两种都接住。

    公开给 `DockerRunner` 复用：两档执行对"超时留下的半截输出"要做的是同一件事。
    """
    if value is None:
        return ""
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else value
