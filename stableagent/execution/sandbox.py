"""执行档的分层（规格 2.4 / ADR-05 决策 7）：本地受限只是收口，容器才是隔离边界。

先把名字说清：`SandboxRunner` 是规格里定下的**协议名**，它约束"怎么调用"，不承诺任何一档
实现提供了隔离。想知道手上这一档有多强，只能读结果里的 `mode` 和实现类的文档。
"""

from __future__ import annotations

import asyncio
from typing import Protocol, runtime_checkable

from stableagent.execution.command import CommandRequest, CommandResult, CommandRunner


@runtime_checkable
class SandboxRunner(Protocol):
    """命令执行器的共同形状（规格 2.4 原样）：`await run(request) -> CommandResult`。

    异步是接口的一部分：一次命令最长可以跑到它的超时（默认 60 秒），而编排层的驱动循环在
    同一轮里还要调度别的 Ticket——一个命令的时长不能停住整条循环。
    """

    async def run(self, request: CommandRequest) -> CommandResult: ...


class LocalRestrictedRunner:
    """`local-restricted` 档：allowlist + cwd 收口 + 超时 + 输出上限，**不是安全沙箱**。

    它约束的是"Agent 能发起哪一类动作"：命令名逐字节命中名单、工作目录收口在本 Ticket 的
    worktree、输出有预算、超时会被掐断。它不约束"动作本身作恶的能力"：网络、worktree 之外的
    文件系统、CPU/内存与恶意代码都不在这一档的射程里。需要那一条边界的是 `DockerRunner`
    （WP-5.3）——`CommandResult.mode` 是读侧区分两者的唯一字段，本地这一档永远写
    `local-restricted`。

    本类只做异步包装：把 request 交给内部的 `CommandRunner`，把结果原样交回去。判定、拒绝码、
    脱敏与截断都只在 `execution/command.py` 一处，这里不放第二套规则。文案纪律（D1）由单元
    测试把本类的文档、两条拒绝消息与 `summary()` 一起断言，不靠注释自觉。
    """

    def __init__(self, runner: CommandRunner) -> None:
        self._runner = runner

    async def run(self, request: CommandRequest) -> CommandResult:
        """同步的 spawn 下沉到线程：阻塞 worker 线程而不是驱动循环（口径同 ADR-02 的 sqlite3）。"""
        return await asyncio.to_thread(self._runner.run, request)
