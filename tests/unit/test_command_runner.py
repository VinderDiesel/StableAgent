"""C8 + L7（环境半边）与 C7（步骤 2.6）：子进程环境是白名单而不是宿主复印件，
命令边界是 allowlist + cwd 收口 + 超时 + 输出上限 + 脱敏，被拒不抛异常也不 spawn。

Task 6 落 `build_child_env`，Task 7 落 `CommandRunner` 并把它接进真实的 `subprocess.run`。
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
from collections.abc import Mapping, Sequence

from stableagent.execution.command import CommandContext, CommandRequest, CommandRunner
from stableagent.execution.environment import build_child_env
from tests.support import FixedClock

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


#: macOS 的 libsystem 在 exec 时自己往环境里加 `__CF_USER_TEXT_ENCODING`（值形如 `0x1F5:0x0:0x0`），
#: 与我们传的 `env` 无关，Linux CI 上不存在。"孩子没有第四个来源"这句话只能带着这份名单说。
OS_INJECTED_KEYS = {"__CF_USER_TEXT_ENCODING"}


def test_child_process_sees_only_the_allowlist(tmp_path: pathlib.Path) -> None:
    """C8 的"子进程可见"半边：真起一个解释器，让它自己打印 `os.environ`。

    白名单不是 `build_child_env` 的返回值，而是孩子实际拿到的那张表——传给 `subprocess.run` 的
    环境即使就是那份 13 键宿主快照的产物，孩子手里也只有七键（外加系统自己加的那一类）。
    """
    worktree = tmp_path / "worktrees" / "sa-01234567-add-rate-limiter"
    worktree.mkdir(parents=True)
    probe = "import json,os;print(json.dumps(dict(os.environ)))"

    done = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=worktree,
        env=_build(worktree),
        shell=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert done.returncode == 0, done.stderr
    seen = json.loads(done.stdout)
    assert set(seen) >= CHILD_KEYS
    assert set(seen) - CHILD_KEYS <= OS_INJECTED_KEYS, set(seen) - CHILD_KEYS
    assert seen["STABLEAGENT_WORKTREE"] == str(worktree)
    assert seen["PATH"] == "/usr/bin:/bin" and seen["LANG"] == "C.UTF-8"
    for banned in ("GITHUB_TOKEN", "AWS_SECRET_ACCESS_KEY", "SSH_AUTH_SOCK", "HOME", "PYTHONPATH"):
        assert banned not in seen, banned


# ---------------------------- C7：allowlist / cwd 收口 / 超时 / 输出上限 / 脱敏（Task 7）


#: 每次读数推进一毫秒级步长：`started_at` 与 `ended_at` 的关系由时钟说了算，不碰墙钟。
class AdvancingClock:
    def __init__(self, start: int = 1_700_000_000_000, step: int = 7) -> None:
        self._now = start
        self._step = step

    def now_ms(self) -> int:
        value = self._now
        self._now += self._step
        return value


def _context(**changes: str) -> CommandContext:
    fields: dict[str, str] = {
        "ticket_id": TICKET_ID,
        "assignment_id": ASSIGNMENT_ID,
        "repository": "acme/widgets",
        "base_ref": "main",
    }
    fields.update(changes)
    return CommandContext(**fields)


def _worktree(tmp_path: pathlib.Path) -> pathlib.Path:
    worktree = tmp_path / "worktrees" / "sa-01234567-add-rate-limiter"
    worktree.mkdir(parents=True)
    return worktree


def _request(worktree: pathlib.Path, *argv: str, timeout_s: float | None = None) -> CommandRequest:
    return CommandRequest(argv=tuple(argv), cwd=worktree, context=_context(), timeout_s=timeout_s)


def _runner(
    tmp_path: pathlib.Path,
    allowlist: Sequence[str],
    *,
    clock: object | None = None,
    **kwargs: object,
) -> CommandRunner:
    return CommandRunner(
        frozenset(allowlist),
        worktree_root=tmp_path / "worktrees",
        clock=clock if clock is not None else FixedClock(),
        **kwargs,
    )


def test_allowed_command_runs_inside_its_worktree(tmp_path: pathlib.Path) -> None:
    """放行路径的形状：exit 0、无拒绝原因、cwd 就是被收口后的 worktree。"""
    worktree = _worktree(tmp_path)
    runner = _runner(tmp_path, [sys.executable], clock=AdvancingClock())

    result = runner.run(
        _request(worktree, sys.executable, "-c", "import os;print('hello');print(os.getcwd())")
    )

    assert result.denied_reason is None and result.exit_code == 0
    assert not result.timed_out and not result.truncated
    assert "hello" in result.stdout
    assert str(worktree) in result.stdout
    assert result.argv == (sys.executable, "-c", "import os;print('hello');print(os.getcwd())")
    assert result.ended_at > result.started_at  # 真跑过：两次读数之间推进过


def test_denied_reasons_are_stable_codes_not_prose(tmp_path: pathlib.Path) -> None:
    """拒绝原因写的是码，不是文案——outbox 与 Report 按码映射，改文案不算改契约。"""
    worktree = _worktree(tmp_path)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    runner = _runner(tmp_path, ["pytest"])

    cases = [
        (_request(worktree), "empty_argv"),
        (_request(worktree, "rm", "-rf", "/"), "command_not_allowed"),
        (CommandRequest(argv=("pytest",), cwd=outside, context=_context()), "cwd_escape"),
        # 两条都不合规时按实现顺序报第一条：① cwd 收口 ② argv ③ allowlist
        (CommandRequest(argv=("rm",), cwd=outside, context=_context()), "cwd_escape"),
    ]
    for request, reason in cases:
        result = runner.run(request)
        assert result.denied_reason == reason, (request.argv, request.cwd)
        assert result.exit_code is None and result.stdout == "" and result.stderr == ""
        assert not result.timed_out and not result.truncated
        assert all(part.islower() and part.isidentifier() for part in reason.split("_"))


def test_denied_command_has_no_side_effect(tmp_path: pathlib.Path) -> None:
    """拒绝发生在 spawn 之前：名单外的命令连文件都没来得及写。"""
    worktree = _worktree(tmp_path)

    result = _runner(tmp_path, []).run(
        _request(worktree, sys.executable, "-c", "open('pwned.txt', 'w').write('x')")
    )

    assert result.denied_reason == "command_not_allowed"
    assert not (worktree / "pwned.txt").exists()
    assert result.started_at == result.ended_at  # 没 spawn：只有一次读数


def test_cwd_escape_denies_before_any_spawn(tmp_path: pathlib.Path) -> None:
    """root 外的 cwd 被拒且不在那里留痕——收口口径与 `confine` 完全一致（C4 复用）。"""
    outside = tmp_path / "elsewhere"
    outside.mkdir()

    result = _runner(tmp_path, [sys.executable]).run(
        CommandRequest(
            argv=(sys.executable, "-c", "open('pwned.txt', 'w').write('x')"),
            cwd=outside,
            context=_context(),
        )
    )

    assert result.denied_reason == "cwd_escape"
    assert not (outside / "pwned.txt").exists()


def test_allowlist_matches_argv_zero_byte_for_byte(tmp_path: pathlib.Path) -> None:
    """精确匹配：不取 basename、不做 PATH 归一化——名单与调用方必须逐字节同形，宁拒不解。"""
    worktree = _worktree(tmp_path)
    probe = "import os;open('ran.txt', 'w').write(os.getcwd())"

    absolute = _runner(tmp_path, ["python3"]).run(_request(worktree, sys.executable, "-c", probe))
    bare = _runner(tmp_path, [sys.executable]).run(_request(worktree, "python3", "-c", probe))

    assert (absolute.denied_reason, bare.denied_reason) == (
        "command_not_allowed",
        "command_not_allowed",
    )
    assert not (worktree / "ran.txt").exists()


def test_timeout_has_no_exit_code_but_keeps_partial_output(tmp_path: pathlib.Path) -> None:
    """超时不是拒绝也不是成功：`exit_code is None`、`timed_out=True`，已产出的部分照样进结果。"""
    worktree = _worktree(tmp_path)

    result = _runner(tmp_path, [sys.executable]).run(
        _request(
            worktree,
            sys.executable,
            "-c",
            "import sys,time;sys.stdout.write('partial\\n');sys.stdout.flush();time.sleep(2)",
            timeout_s=0.2,
        )
    )

    assert result.timed_out and result.denied_reason is None and result.exit_code is None
    assert "partial" in result.stdout


def test_default_timeout_applies_when_the_request_does_not_ask(tmp_path: pathlib.Path) -> None:
    """请求不填 `timeout_s` 时走 runner 的默认值：0.1s 的默认值就足以掐掉一个 sleep(2)。"""
    worktree = _worktree(tmp_path)

    result = _runner(tmp_path, [sys.executable], timeout_s=0.1).run(
        _request(worktree, sys.executable, "-c", "import time;time.sleep(2)", timeout_s=None)
    )

    assert result.timed_out and result.exit_code is None


def test_output_over_the_cap_is_truncated_within_budget(tmp_path: pathlib.Path) -> None:
    """上限含尾标：200KB 的 stdout 收进 64KB 预算，尾标报出被丢的字节数，stderr 不受牵连。"""
    worktree = _worktree(tmp_path)

    result = _runner(tmp_path, [sys.executable]).run(
        _request(
            worktree,
            sys.executable,
            "-c",
            "import sys;sys.stdout.write('x'*200_000);sys.stderr.write('y'*100)",
        )
    )

    assert result.truncated and result.exit_code == 0
    assert len(result.stdout.encode("utf-8")) <= 64 * 1024
    assert result.stdout.endswith("]") and "[truncated " in result.stdout
    assert result.stderr == "y" * 100  # 没超上限的流原样保留


def test_tiny_cap_stays_within_its_own_budget(tmp_path: pathlib.Path) -> None:
    """上限可配到很小：尾标吃进预算后总长仍不越界，头部内容照样保住。"""
    worktree = _worktree(tmp_path)

    result = _runner(tmp_path, [sys.executable], max_output_bytes=200).run(
        _request(worktree, sys.executable, "-c", "print('z'*500)")
    )

    assert result.truncated
    assert len(result.stdout.encode("utf-8")) <= 200
    assert result.stdout.startswith("z" * 100)


def test_secrets_are_redacted_before_truncation(tmp_path: pathlib.Path) -> None:
    """顺序可证：先脱敏再截断。若反过来，落在切口前的 `SEKRET` 就会原样进 Report。"""
    worktree = _worktree(tmp_path)
    script = (
        "import sys;sys.stdout.write('A'*100 + 'SEKRET' + 'A'*100);sys.stderr.write('err SEKRET')"
    )

    result = _runner(tmp_path, [sys.executable], max_output_bytes=128, redact=("SEKRET",)).run(
        _request(worktree, sys.executable, "-c", script)
    )

    assert "SEKRET" not in result.stdout + result.stderr
    assert "***" in result.stdout and result.stderr == "err ***"
    assert result.truncated


def test_empty_redaction_entries_are_ignored(tmp_path: pathlib.Path) -> None:
    """空串不进 replace：`''.replace('', '***')` 会在每个字符间插星号，那是自伤。"""
    worktree = _worktree(tmp_path)

    only_empty = _runner(tmp_path, [sys.executable], redact=("",)).run(
        _request(worktree, sys.executable, "-c", "print('SEKRET')")
    )
    mixed = _runner(tmp_path, [sys.executable], redact=("", "SEKRET")).run(
        _request(worktree, sys.executable, "-c", "print('SEKRET')")
    )

    assert only_empty.stdout.strip() == "SEKRET"
    assert mixed.stdout.strip() == "***"


def test_nonzero_exit_is_a_result_not_a_denial(tmp_path: pathlib.Path) -> None:
    """退出码非零是命令自己的结论：不设 `denied_reason`，也不抛——判定权在调用方。"""
    worktree = _worktree(tmp_path)

    result = _runner(tmp_path, [sys.executable]).run(
        _request(worktree, sys.executable, "-c", "import sys;sys.exit(3)")
    )

    assert (result.exit_code, result.denied_reason, result.timed_out) == (3, None, False)


def test_spawned_child_sees_the_request_context(tmp_path: pathlib.Path) -> None:
    """接线证据（不是 C8 的第二次证明）：`build_child_env` 真被 spawn 用上，
    且 `STABLEAGENT_WORKTREE` 是收口后的路径而不是调用方递进来的原始字符串。"""
    worktree = _worktree(tmp_path)

    result = _runner(tmp_path, [sys.executable]).run(
        _request(
            worktree, sys.executable, "-c", "import json,os;print(json.dumps(dict(os.environ)))"
        )
    )

    seen = json.loads(result.stdout)
    assert seen["STABLEAGENT_TICKET_ID"] == TICKET_ID
    assert seen["STABLEAGENT_ASSIGNMENT_ID"] == ASSIGNMENT_ID
    assert seen["STABLEAGENT_REPOSITORY"] == "acme/widgets"
    assert seen["STABLEAGENT_BASE_REF"] == "main"
    assert seen["STABLEAGENT_WORKTREE"] == str(worktree.resolve())
    assert set(seen) - CHILD_KEYS <= OS_INJECTED_KEYS, set(seen) - CHILD_KEYS


def test_relative_cwd_is_confined_into_the_root(tmp_path: pathlib.Path) -> None:
    """cwd 允许写成相对 root 的形式：收口发生在 runner 里，child 看到的是绝对路径。"""
    worktree = _worktree(tmp_path)
    relative = pathlib.Path(worktree.name)

    result = _runner(tmp_path, [sys.executable]).run(
        CommandRequest(
            argv=(sys.executable, "-c", "import os;print(os.getcwd())"),
            cwd=relative,
            context=_context(),
        )
    )

    assert result.stdout.strip() == str(worktree.resolve())
