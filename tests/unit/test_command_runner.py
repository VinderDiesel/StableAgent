"""C8 + L7（环境半边）：子进程环境是白名单，不是宿主环境的复印件。

Task 6 只管 `build_child_env`；Task 7 的 C7 五项（allowlist / cwd / 超时 / 输出上限 / 脱敏）
追加在本文件。
"""

from __future__ import annotations

import os
import pathlib
from collections.abc import Mapping

from stableagent.execution.environment import build_child_env

#: 宿主里常见、漏进子进程就等于把凭据或宿主代码路径交给 Agent 命令的变量。
HOSTILE_HOST = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/home/agent",
    "GITHUB_TOKEN": "ghp_SEKRET",
    "GH_TOKEN": "ghu_SEKRET",
    "OPENAI_API_KEY": "sk-SEKRET",
    "AWS_SECRET_ACCESS_KEY": "wJalrXUtnFEMI",
    "AWS_ACCESS_KEY_ID": "AKIAIOSFODNN7EXAMPLE",
    "SSH_AUTH_SOCK": "/run/user/1000/key/ssh",
    "PYTHONPATH": "/opt/stableagent/src",
    "STABLEAGENT_DB_PATH": "/var/lib/stableagent/orchestration.db",
    "LANG": "zh_CN.GBK",  # 宿主语言设置不带走：固定值说了算
    "ENV": "eval $(echo attack)",  # 值里带 shell 语法也不带走
    "PAGER": "less",
}

#: 白名单的全部内容——五个上下文变量加两个非敏感运行变量，多一个都是越权。
CHILD_KEYS = {
    "STABLEAGENT_TICKET_ID",
    "STABLEAGENT_ASSIGNMENT_ID",
    "STABLEAGENT_WORKTREE",
    "STABLEAGENT_REPOSITORY",
    "STABLEAGENT_BASE_REF",
    "PATH",
    "LANG",
}

TICKET_ID = "tk-0123456789abcdef0123456789abcdef"
ASSIGNMENT_ID = "as-0123456789abcdef"


def _build(
    worktree: pathlib.Path, *, host: Mapping[str, str] | None = HOSTILE_HOST
) -> dict[str, str]:
    return build_child_env(
        ticket_id=TICKET_ID,
        assignment_id=ASSIGNMENT_ID,
        worktree=worktree,
        repository="acme/widgets",
        base_ref="main",
        host=host,
    )


def test_child_env_is_an_allowlist(tmp_path: pathlib.Path) -> None:
    """键集合就是白名单本身：宿主环境里的密钥、凭据助手、代码路径一个都不漏进来。"""
    env = _build(tmp_path / "worktrees" / "sa-01234567-add-rate-limiter")

    assert set(env) == CHILD_KEYS
    for leaked in HOSTILE_HOST:
        if leaked not in CHILD_KEYS:
            assert leaked not in env, leaked
    for banned in ("ghp_SEKRET", "ghu_SEKRET", "sk-SEKRET", "wJalrXUtnFEMI", "AKIAIOSFODNN7"):
        assert banned not in " ".join(env.values()), banned
    assert env["PATH"] == "/usr/bin:/bin"  # 取自宿主：解析可执行文件必需，且不硬编码目录
    assert env["LANG"] == "C.UTF-8"  # 固定值，不继承宿主的 zh_CN.GBK
    assert all(isinstance(value, str) for value in env.values())


def test_child_env_leaves_the_database_and_host_home_behind(tmp_path: pathlib.Path) -> None:
    """规格 §2.3 R5：Agent 只拿到本 Ticket 的 worktree 路径——P4 的库、宿主 HOME 都不在值里。"""
    values = list(_build(tmp_path / "wt").values())

    assert not any("orchestration.db" in value for value in values)
    assert not any("/home/agent" in value for value in values)
    assert not any("/run/user" in value for value in values)
    assert not any("/opt/stableagent" in value for value in values)


def test_child_env_has_no_secrets_from_arguments(tmp_path: pathlib.Path) -> None:
    """参数按原值透传：整份环境可以直接写死比对，没有第四个来源掺进来。"""
    worktree = tmp_path / "worktrees" / "sa-01234567-add-rate-limiter"

    assert _build(worktree) == {
        "STABLEAGENT_TICKET_ID": TICKET_ID,
        "STABLEAGENT_ASSIGNMENT_ID": ASSIGNMENT_ID,
        "STABLEAGENT_WORKTREE": str(worktree),
        "STABLEAGENT_REPOSITORY": "acme/widgets",
        "STABLEAGENT_BASE_REF": "main",
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
    }


def test_child_env_without_host_reads_only_path_from_the_process_environment() -> None:
    """生产路径（不注入 host）：整份 `os.environ` 里只取 `PATH`，连必然存在的 `HOME` 也不带走。"""
    env = _build(pathlib.Path("/worktrees/sa-01234567-add-rate-limiter"), host=None)

    assert set(env) == CHILD_KEYS
    assert env["PATH"] == os.environ.get("PATH", "")
    assert env["LANG"] == "C.UTF-8"
    for banned in ("HOME", "GITHUB_TOKEN", "AWS_SECRET_ACCESS_KEY", "SSH_AUTH_SOCK", "PYTHONPATH"):
        assert banned not in env, banned


def test_child_env_does_not_invent_a_path_when_the_host_has_none(tmp_path: pathlib.Path) -> None:
    """宿主没有 `PATH` 时留空：白名单不硬编码 `/usr/local/bin` 之类的目录当默认值。"""
    env = build_child_env(
        ticket_id=TICKET_ID,
        assignment_id=ASSIGNMENT_ID,
        worktree=tmp_path / "wt",
        repository="acme/widgets",
        base_ref="main",
        host={"LANG": "C", "HOME": "/home/agent"},
    )

    assert env["PATH"] == ""
    assert set(env) == CHILD_KEYS
