"""子进程环境的白名单（规格 2.3 R5 + 2.4，L7 的环境半边）。

产出七个变量：五个 `STABLEAGENT_*` 上下文 + `PATH` + `LANG`，其余一律不带。白名单的意义不在
"过滤掉已知密钥名"，而在"没有第四个来源"——宿主 `os.environ` 里有什么、Agent 拿不到什么，
这条推断与宿主的变量名单无关，所以新增一个宿主密钥不需要改这里。

两处取舍写在名字旁边：

* `PATH`：非敏感，且是解析可执行文件所必需的——没有它 `pytest`/`python` 这类命令名根本找不到。
  值取自宿主而不是硬编码目录列表（硬编码等于替宿主决定哪些二进制可信）；宿主没有 `PATH` 时留空，
  宁可不猜。
* `LANG`：固定 `C.UTF-8`，不继承宿主。① 宿主的 `zh_CN.GBK` 之类会改变子进程的字节输出，Report
  里就无法统一按 UTF-8 解释与脱敏；② 这里与 `git.py` 的 `git_env()`（`LANG=C`）刻意不同：git 只吐
  我们按行解析的 plumbing 输出，`C` 更稳；Agent 命令要吐中文标题与 diff，需要 UTF-8 的 locale。

不带走的东西各有归属：`HOME`（`~/.git-credentials`、`~/.aws/credentials` 都挂在它下面，
`git_env()` 同样不注入）、`*_TOKEN`/`*_KEY`、`SSH_AUTH_SOCK`（转发出去的凭据代理）、
`PYTHONPATH`（宿主代码路径直通子进程 import）、`ENV`/`BASHOPTS` 这类 shell 会自动求值的入口，
以及 P4 的 SQLite 路径与其他 Ticket 的目录——规格 2.3 R5 说 Agent 只获取 worktree 路径和
非敏感变量，多给一个就是越权。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

#: 不随宿主语言设置走：Report 里的字节要能统一按 UTF-8 解释。
CHILD_LANG = "C.UTF-8"


def build_child_env(
    *,
    ticket_id: str,
    assignment_id: str,
    worktree: Path,
    repository: str,
    base_ref: str,
    host: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """构造 Agent 命令的子进程环境；`host` 注入只为测试断言"其余变量一个不漏"。

    参数按原值透传（`worktree` 取其字符串形态），不脱敏也不截断：它们是标识符，不是内容——
    凭据不该出现在这些位置上，`execution.worktree` 记录的 remote 身份已在传输层剥掉 userinfo。
    """
    source = os.environ if host is None else host
    return {
        "STABLEAGENT_TICKET_ID": ticket_id,
        "STABLEAGENT_ASSIGNMENT_ID": assignment_id,
        "STABLEAGENT_WORKTREE": str(worktree),
        "STABLEAGENT_REPOSITORY": repository,
        "STABLEAGENT_BASE_REF": base_ref,
        "PATH": source.get("PATH", ""),
        "LANG": CHILD_LANG,
    }
