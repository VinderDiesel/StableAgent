"""容器档执行（规格 2.5 / ADR-05 决策 7）：默认装配是零网络、只读根、drop 全部 cap。

先说清这一档兑现了什么：`DockerRunner` 把 `docker run` 的参数钉死成一份**默认封闭**的数组——
`--network none`、`--read-only`、`--tmpfs /tmp`、pid/内存/CPU 上限、`no-new-privileges`、
`--cap-drop ALL`、只挂本 Ticket 的 worktree。网络只在调用方给出**已定义**的 profile 时才开，
且 profile 名会原样进 `--network`，所以 `host`/`none`/`bridge`/`container` 这些 Docker 保留名
一律拒绝——写了也不给用，宁拒不解。

四条口径写在名字旁边：

* **判定不另起一套**：四条命令判定（`cwd_escape`/`empty_argv`/`command_not_allowed`/`shell_syntax`）
  走 `command.py` 的 `deny_reason`，脱敏与截断走 `fit_output`，拒绝的出口走 `denied_result`。
  容器档若带一份自己的复制品，两边迟早漂出"本地拒、容器放"的缝。
* **`-e` 只带键名、不带值**（L7）：五个 `STABLEAGENT_*` 的值留在交给 docker CLI 的那份白名单环境
  里（`build_child_env` 产出的七项），不进宿主 argv。宿主 argv 是同机所有用户可读的，而
  `/proc/<pid>/environ` 不是；把值写进 `-e KEY=VALUE` 等于把身份贴出去。反过来也不把 `PATH`
  转发进容器——那会让容器按宿主的目录顺序找可执行文件。
* **镜像必须 pin 住**（规格 2.5 第 4 条）：不带 tag 等于 `:latest`，`latest` 本身就是浮动的，
  两者都拒；`name:1.2.3` 收，`name@sha256:<64hex>` 是推荐写法。**digest pin 指南**：发布镜像后
  用 `docker inspect --format='{{index .RepoDigests 0}}' <tag>` 取回 digest 写进配置；digest 变了
  就是另一份文件系统，配置里换 digest 而不是换 tag，也不给 `latest` 留任何默认位。
* **超时是客户端超时**：`timeout_s` 掐的是宿主上那个 `docker run` 客户端。客户端被杀不等于容器
  被杀——容器是否随之退出取决于 Docker 版本的信号处理，而本轮一个容器都不启动（L8）。同理
  不传 `--name`：固定名要处理并发冲突，随机名又让超时后的容器无法定点清理。这两条留给 Task 11
  的 `docker` marker + `STABLEAGENT_DOCKER=1` 双条件用例去验，验不过就在这一档补清理路径。
"""

from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from stableagent.execution.command import (
    CommandRequest,
    CommandResult,
    _no_audit,
    denied_result,
    deny_reason,
    fit_output,
    stream_text,
)
from stableagent.execution.environment import build_child_env
from stableagent.execution.errors import CommandDeniedError
from stableagent.execution.paths import confine
from stableagent.orchestration.model import Clock

#: 容器档的结果模式名。`CommandResult.mode` 是读侧区分两档的唯一字段（Task 8 钉下的口径），
#: 沿用 `local-restricted` 就等于让容器冒充本地。
DOCKER = "docker"

#: 容器内的工作目录：挂载点与 `-w` 必须同值，否则命令的 cwd 落在挂载之外。
CONTAINER_WORKDIR = "/workspace"

#: Docker 的保留网络名：profile 名会原样进 `--network`，这些名字能把封闭性一句话取消。
RESERVED_NETWORKS = frozenset({"host", "none", "bridge", "container"})

#: tag 大小写两种写法都算浮动——Docker 的 tag 区分大小写，但 `LATEST` 解析出的还是同一个浮动指针。
FLOATING_TAGS = frozenset({"latest"})

#: 会随 `STABLEAGENT_` 前缀进容器的键；`PATH`/`LANG` 只给 CLI，不给容器。
_CONTAINER_ENV_PREFIX = "STABLEAGENT_"


class DockerRunner:
    """容器档的命令执行器：判定与文本口径复用 `command.py`，本类只负责装配与 spawn。"""

    def __init__(
        self,
        image: str,
        *,
        allowlist: Iterable[str],
        worktree_root: Path,
        network_profiles: Mapping[str, Sequence[str]] | None = None,
        network_profile: str | None = None,
        clock: Clock,
        cpus: float = 1.0,
        memory: str = "512m",
        pids_limit: int = 128,
        timeout_s: float = 300.0,
        max_output_bytes: int = 64 * 1024,
        redact: tuple[str, ...] = (),
        audit=_no_audit,
    ) -> None:
        """`network_profile` 挂在 runner 上而不是 request 上：协议 `run(request)` 只有一个形参，
        而档位参数（名单、资源上限、网络 profile）本就该随执行档配置走。给 `CommandRequest`
        加一个 `network_profile` 字段会让 `LocalRestrictedRunner` 收到一个它无法兑现的意图——
        那种"看起来被限制了"的错觉比没有更糟。

        `network_profiles` 的值（origin 名单）在本轮不参与装配：Docker 没有 origin 级的出口控制，
        名单的强制点在授权层（A5 的 `network_profile` 判定）与将来的 egress 代理上。留这个字段
        是为了让配置自己说明"这个名字允许访问什么"，别让 `--network buildnet` 变成无源之名。
        """
        self._image = image
        self._allowlist = frozenset(allowlist)
        self._worktree_root = Path(worktree_root)
        self._network_profiles = dict(network_profiles or {})
        self._network_profile = network_profile
        self._clock = clock
        self._cpus = cpus
        self._memory = memory
        self._pids_limit = pids_limit
        self._timeout_s = timeout_s
        self._max_output_bytes = max_output_bytes
        self._secrets = tuple(redact)
        self._audit = audit

    async def run(
        self, request: CommandRequest, *, network_profile: str | None = None
    ) -> CommandResult:
        """协议实现：装配 + spawn 下沉到线程，一条命令的时长不停住编排层的驱动循环。

        `network_profile` 省略时取 runner 的配置值；显式传值只影响这一次装配。两档执行都不接
        受"调用方给一条命令换个未定义的 profile"——profile 名必须先在 `network_profiles` 里定义过。
        """
        profile = self._network_profile if network_profile is None else network_profile
        return await asyncio.to_thread(self._execute, request, profile)

    def build_argv(
        self, request: CommandRequest, *, network_profile: str | None = None
    ) -> list[str]:
        """装配 `docker run` 参数数组——纯构造，不 spawn；不合规的输入一律抛稳定码异常。

        这里是装配而不是执行，所以失败形态是异常（`CommandDeniedError`），不是"结果里带
        `denied_reason`"：拒绝作为结果只在 `run()` 这条路上成立。四条命令判定在这里再走一遍
        `deny_reason`，为的是让直接调用装配的人拿到的也是同一份规则，不是"装配成功、执行才拒"。
        """
        reason = deny_reason(request, allowlist=self._allowlist, worktree_root=self._worktree_root)
        if reason is not None:
            raise CommandDeniedError(f"command_denied: {reason}", request.argv)
        self._check_image(request.argv)
        network = self._check_network(network_profile, request.argv)
        worktree = confine(self._worktree_root, request.cwd)
        argv = [
            DOCKER,
            "run",
            "--rm",
            "--network",
            network,
            "--read-only",
            "--tmpfs",
            "/tmp",
            "--pids-limit",
            str(self._pids_limit),
            "--memory",
            self._memory,
            "--cpus",
            str(self._cpus),
            "--security-opt",
            "no-new-privileges",
            "--cap-drop",
            "ALL",
            "-v",
            f"{worktree}:{CONTAINER_WORKDIR}",
            "-w",
            CONTAINER_WORKDIR,
        ]
        for key in self._container_env(request):
            argv += ["-e", key]
        argv += [self._image, *request.argv]
        return argv

    def _execute(self, request: CommandRequest, network_profile: str | None) -> CommandResult:
        """判定 → 装配 → spawn → 归一化输出：顺序与 `CommandRunner.run` 完全一致。"""
        reason = deny_reason(request, allowlist=self._allowlist, worktree_root=self._worktree_root)
        if reason is not None:
            return denied_result(request, reason, clock=self._clock, mode=DOCKER, audit=self._audit)
        argv = self.build_argv(request, network_profile=network_profile)
        env = self._cli_env(request)
        timeout = request.timeout_s if request.timeout_s is not None else self._timeout_s
        started = self._clock.now_ms()
        try:
            done = subprocess.run(
                argv,
                env=env,
                timeout=timeout,
                shell=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            stdout, first_cut = self._fit(stream_text(exc.stdout))
            stderr, second_cut = self._fit(stream_text(exc.stderr))
            return self._result(
                request,
                exit_code=None,
                stdout=stdout,
                stderr=stderr,
                started_at=started,
                truncated=first_cut or second_cut,
                timed_out=True,
            )
        stdout, first_cut = self._fit(done.stdout)
        stderr, second_cut = self._fit(done.stderr)
        return self._result(
            request,
            exit_code=done.returncode,
            stdout=stdout,
            stderr=stderr,
            started_at=started,
            truncated=first_cut or second_cut,
            timed_out=False,
        )

    def _result(
        self,
        request: CommandRequest,
        *,
        exit_code: int | None,
        stdout: str,
        stderr: str,
        started_at: int,
        truncated: bool,
        timed_out: bool,
    ) -> CommandResult:
        """成功/超时两档共用的结果装配：正文已在 `_fit` 里脱敏并收进预算。

        `argv` 记的是**请求**的 argv，不是装配出的 docker argv——后者含宿主挂载路径，
        进 Report 就是把宿主目录结构长期留存（L7 的 Report 半边）。
        """
        return CommandResult(
            argv=request.argv,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            started_at=started_at,
            ended_at=self._clock.now_ms(),
            truncated=truncated,
            timed_out=timed_out,
            denied_reason=None,
            mode=DOCKER,
        )

    def _check_image(self, argv: tuple[str, ...]) -> None:
        """浮动镜像在装配这一步就拒：生产默认不浮动（规格 2.5 第 4 条）。

        放在装配期而不是构造期：`CommandDeniedError` 的文案要带 argv，构造期没有；代价是配错镜像
        要到第一次装配才报，换来的是"哪些请求被什么规则拒了"只有一个出处。
        """
        if "@" in self._image:
            return  # digest pin：内容寻址，浮动与否无关
        tail = self._image.rsplit("/", 1)[-1]
        tag = tail.rsplit(":", 1)[1] if ":" in tail else ""
        if not tag or tag.lower() in FLOATING_TAGS:
            raise CommandDeniedError(f"image_not_pinned: {self._image}", argv)

    def _check_network(self, network_profile: str | None, argv: tuple[str, ...]) -> str:
        """没给 profile 就是 `none`；给了就必须定义过、且不是 Docker 的保留名。"""
        if network_profile is None:
            return "none"
        if network_profile in RESERVED_NETWORKS:
            raise CommandDeniedError(f"network_profile_reserved: {network_profile}", argv)
        if network_profile not in self._network_profiles:
            raise CommandDeniedError(f"network_profile_undefined: {network_profile}", argv)
        return network_profile

    def _container_env(self, request: CommandRequest) -> tuple[str, ...]:
        """该进容器的键名：CLI 那七项里带 `STABLEAGENT_` 前缀的五个，值由 CLI 的环境转发。"""
        return tuple(key for key in self._cli_env(request) if key.startswith(_CONTAINER_ENV_PREFIX))

    def _cli_env(self, request: CommandRequest) -> dict[str, str]:
        """交给 docker CLI 的环境：七键白名单，其中 `STABLEAGENT_WORKTREE` 写**容器内**路径。

        容器里的进程问"我的 worktree 在哪"，正确答案是 `/workspace`，不是宿主的
        `/Users/.../worktrees/sa-...`——后者在它自己的文件系统里根本不存在。
        """
        context = request.context
        return build_child_env(
            ticket_id=context.ticket_id,
            assignment_id=context.assignment_id,
            worktree=Path(CONTAINER_WORKDIR),
            repository=context.repository,
            base_ref=context.base_ref,
        )

    def _fit(self, text: str) -> tuple[str, bool]:
        """委托给 `fit_output`：先脱敏后截断，预算含尾标。"""
        return fit_output(text, max_output_bytes=self._max_output_bytes, secrets=self._secrets)
