"""D1（步骤 2.8）+ D2（步骤 2.10）+ D3（步骤 2.11）：叙事纪律、参数装配与 Docker 用例的门禁。

D1 来自规格 §1（"不宣称本地受限模式能隔离恶意代码"）与 ADR-05 决策 7：`LocalRestrictedRunner`
的类文档、被拒时产生的消息、以及进 Report 的一行 summary 三处都不得出现 "sandbox" 一词，
且必须写明 `local-restricted`。断言的是文案，不是行为。

D2 断言的是**参数**：全量测试不启动容器（L8），所以容器档能证的就是"装配出来的 `docker run`
argv 长什么样"与"被拒时不碰 docker CLI"。逐项对上规格 2.5 的默认值：`--network none`、
`--read-only`、`--tmpfs /tmp`、pid/内存/CPU 上限、`no-new-privileges`、`--cap-drop ALL`、
只挂本 worktree、`-e` 只带五个 `STABLEAGENT_*` 的**名字**（值不上宿主 argv）。

Task 8 落"协议 + 本地实现 + summary 形状"，Task 10 落容器档的装配，Task 11 落 Docker 用例的
双条件门禁（步骤 2.11 / D3）：`docker` marker 注册 + `STABLEAGENT_DOCKER=1`，两个条件缺一个
就跳过，且跳过原因在 `pytest -rs` 里可见——本文件的 D3 段用嵌套 pytest 观察真实收集结果。
"""

from __future__ import annotations

import inspect
import os
import pathlib
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import replace

import pytest

from stableagent.execution import docker as docker_module
from stableagent.execution.command import (
    CommandContext,
    CommandRequest,
    CommandResult,
    CommandRunner,
)
from stableagent.execution.docker import DockerRunner
from stableagent.execution.errors import CommandDeniedError
from stableagent.execution.sandbox import LocalRestrictedRunner, SandboxRunner
from tests.support import FixedClock

TICKET_ID = "tk-0001"
ASSIGNMENT_ID = "as-0001"

#: stdout 里出现的敏感串；它一旦进了 summary，就说明长期记录会带走命令正文。
STREAM_MARKER = "MARKER-FROM-STDOUT-STREAM"


def _context() -> CommandContext:
    return CommandContext(
        ticket_id=TICKET_ID,
        assignment_id=ASSIGNMENT_ID,
        repository="acme/widgets",
        base_ref="main",
    )


def _worktree(tmp_path: pathlib.Path) -> pathlib.Path:
    worktree = tmp_path / "worktrees" / "sa-01234567-add-rate-limiter"
    worktree.mkdir(parents=True)
    return worktree


def _request(
    worktree: pathlib.Path,
    *argv: str,
    cwd: pathlib.Path | None = None,
    timeout_s: float | None = None,
) -> CommandRequest:
    return CommandRequest(
        argv=tuple(argv), cwd=cwd or worktree, context=_context(), timeout_s=timeout_s
    )


def _runner(tmp_path: pathlib.Path, allowlist: Sequence[str], **kwargs: object) -> CommandRunner:
    return CommandRunner(
        frozenset(allowlist),
        worktree_root=tmp_path / "worktrees",
        clock=FixedClock(),
        **kwargs,
    )


def _result(**changes: object) -> CommandResult:
    fields: dict[str, object] = {
        "argv": ("python3", "-c", "pass"),
        "exit_code": 0,
        "stdout": "",
        "stderr": "",
        "started_at": 1_700_000_000_000,
        "ended_at": 1_700_000_000_000,
        "truncated": False,
        "timed_out": False,
        "denied_reason": None,
    }
    fields.update(changes)
    return CommandResult(**fields)  # type: ignore[arg-type]


class RecordingRunner:
    """`CommandRunner` 的替身：记下收到的 request，原样返回一个固定结果。"""

    def __init__(self, result: CommandResult) -> None:
        self.calls: list[CommandRequest] = []
        self._result = result

    def run(self, request: CommandRequest) -> CommandResult:
        self.calls.append(request)
        return self._result


def test_local_restricted_never_says_sandbox(tmp_path: pathlib.Path) -> None:
    """D1 的三处文案：类文档 + 两条拒绝消息；不称沙箱，且写明 local-restricted。"""
    worktree = _worktree(tmp_path)
    runner = _runner(tmp_path, ["python3"])

    denied_command = runner.run(_request(worktree, "rm", "-rf", "/"))
    denied_cwd = runner.run(_request(worktree, "python3", cwd=tmp_path / "outside"))
    # 语料必须真的是这两条拒绝，否则文案断言会空转。
    assert denied_command.denied_reason == "command_not_allowed"
    assert denied_cwd.denied_reason == "cwd_escape"

    corpus = {
        "类文档": inspect.getdoc(LocalRestrictedRunner) or "",
        "拒绝消息/命令不在名单": denied_command.summary(),
        "拒绝消息/cwd 越界": denied_cwd.summary(),
    }
    for label, text in corpus.items():
        lowered = text.lower()
        assert "sandbox" not in lowered, f"{label} 把本地受限模式称成了沙箱：{text!r}"
        assert "local-restricted" in lowered, f"{label} 没写明 local-restricted：{text!r}"


async def test_runner_delegates_to_command_runner(tmp_path: pathlib.Path) -> None:
    """本地实现只做一件事：把 request 交给 `CommandRunner`，把它的结果原样交回去。"""
    worktree = _worktree(tmp_path)
    sentinel = _result()
    inner = RecordingRunner(sentinel)
    local = LocalRestrictedRunner(inner)  # type: ignore[arg-type]
    request = _request(worktree, "python3", "-c", "pass")

    result = await local.run(request)

    assert result is sentinel
    assert inner.calls == [request]


def test_local_restricted_matches_the_protocol_without_inheriting_it() -> None:
    """协议靠结构匹配（M2 取向 ①）：`LocalRestrictedRunner` 不继承 `SandboxRunner`。"""
    assert LocalRestrictedRunner.__mro__[1] is object
    assert isinstance(LocalRestrictedRunner(RecordingRunner(_result())), SandboxRunner)  # type: ignore[arg-type]


def test_summary_names_mode_exit_code_and_truncation(tmp_path: pathlib.Path) -> None:
    """summary 的三个必含项：模式、退出码、是否截断——被截的那一份要真的被截。"""
    worktree = _worktree(tmp_path)
    runner = _runner(tmp_path, ["python3"])
    capped = _runner(tmp_path, ["python3"], max_output_bytes=200)

    intact = runner.run(_request(worktree, "python3", "-c", "print('ok')"))
    big = capped.run(_request(worktree, "python3", "-c", f"print('{STREAM_MARKER}' + 'x' * 5000)"))

    assert big.truncated  # 没截就没有断到"是否截断"这一项
    assert "local-restricted" in intact.summary()
    assert "exit=0" in intact.summary()
    assert "intact" in intact.summary()
    assert "local-restricted" in big.summary()
    assert "truncated" in big.summary()


def test_summary_carries_no_stream_text(tmp_path: pathlib.Path) -> None:
    """正文再短也不进 summary：Report 长期留存，stdout 里可能有密钥，也可能根本不是我们的文本。"""
    worktree = _worktree(tmp_path)
    runner = _runner(tmp_path, ["python3"])

    result = runner.run(_request(worktree, "python3", "-c", f"print('{STREAM_MARKER}')"))

    assert STREAM_MARKER in result.stdout
    assert STREAM_MARKER not in result.summary()


def test_denied_summary_names_the_stable_code(tmp_path: pathlib.Path) -> None:
    """被拒的一行写稳定码，不写文案：调用方按码分派，不解析句子。"""
    worktree = _worktree(tmp_path)
    runner = _runner(tmp_path, ["python3"])

    denied = runner.run(_request(worktree, "curl", "http://example.invalid"))

    assert "denied=command_not_allowed" in denied.summary()
    assert "exit=" not in denied.summary()


def test_timeout_summary_names_the_missing_exit_code(tmp_path: pathlib.Path) -> None:
    """超时那一档不写 `exit=None`：事实是"没有退出码"，不是"退出码是空"。"""
    worktree = _worktree(tmp_path)
    runner = _runner(tmp_path, ["python3"])

    timed_out = runner.run(
        _request(worktree, "python3", "-c", "__import__('time').sleep(5)", timeout_s=0.05)
    )

    assert timed_out.timed_out and timed_out.exit_code is None
    summary = timed_out.summary()
    assert "timed_out" in summary and "exit=" not in summary
    assert "local-restricted" in summary


# --------------------------------------------------------------------------- D2（步骤 2.10）

IMAGE = "example.invalid/sa-runner@sha256:" + "0" * 64
CONTAINER_WORKDIR = "/workspace"

#: 容器里该看见的五个变量：`PATH`/`LANG` 不在内——转发宿主 `PATH` 等于让容器按宿主的目录顺序
#: 找可执行文件，`LANG` 则由镜像自己决定。
CONTAINER_KEYS = [
    "STABLEAGENT_TICKET_ID",
    "STABLEAGENT_ASSIGNMENT_ID",
    "STABLEAGENT_WORKTREE",
    "STABLEAGENT_REPOSITORY",
    "STABLEAGENT_BASE_REF",
]


def _docker_runner(
    tmp_path: pathlib.Path, *, image: str = IMAGE, **changes: object
) -> DockerRunner:
    settings: dict[str, object] = {
        "allowlist": frozenset(["python3"]),
        "worktree_root": tmp_path / "worktrees",
        "clock": FixedClock(),
    }
    settings.update(changes)
    return DockerRunner(image, **settings)  # type: ignore[arg-type]


class FakeSpawn:
    """替 `subprocess.run` 记账：容器档的测试只看装配结果被怎么交出去，绝不启动 Docker（L8）。

    `raise_once` 是给超时清理那条路准备的：第一次 spawn 抛 `TimeoutExpired`（那是被掐的
    `docker run`），后续调用正常返回——不然清理那条调用发没发出去都测不到。
    """

    def __init__(
        self,
        *,
        stdout: str = "",
        stderr: str = "",
        returncode: int = 0,
        raises: BaseException | None = None,
        raise_once: bool = False,
    ) -> None:
        self.calls: list[tuple[list[str], dict[str, object]]] = []
        self._stdout = stdout
        self._stderr = stderr
        self._returncode = returncode
        self._raises = raises
        self._raise_once = raise_once

    def __call__(self, argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        self.calls.append((list(argv), kwargs))
        if self._raises is not None and not (self._raise_once and len(self.calls) > 1):
            raise self._raises
        return subprocess.CompletedProcess(
            args=list(argv), returncode=self._returncode, stdout=self._stdout, stderr=self._stderr
        )


def _flag_values(argv: list[str], flag: str) -> list[str]:
    """`flag` 后面那一项的全体取值；挂载与 `-e` 会多次出现，其余应当只出现一次。"""
    return [
        argv[index + 1] for index, item in enumerate(argv) if item == flag and index + 1 < len(argv)
    ]


def _flag_value(argv: list[str], flag: str) -> str:
    values = _flag_values(argv, flag)
    assert len(values) == 1, f"{flag} 出现了 {len(values)} 次：{argv}"
    return values[0]


def test_docker_argv_defaults_are_locked_down(tmp_path: pathlib.Path) -> None:
    """规格 2.5 的默认装配逐项对上：零网络、只读根、pid/内存/CPU 上限、drop 全部 cap。"""
    worktree = _worktree(tmp_path)
    request = _request(worktree, "python3", "-c", "print('ok')")

    argv = _docker_runner(tmp_path).build_argv(request)

    assert argv[:2] == ["docker", "run"]
    assert "--rm" in argv
    assert _flag_value(argv, "--network") == "none"
    assert "--read-only" in argv
    assert _flag_value(argv, "--tmpfs") == "/tmp"
    assert _flag_value(argv, "--pids-limit") == "128"
    assert _flag_value(argv, "--memory") == "512m"
    assert _flag_value(argv, "--cpus") == "1.0"
    assert _flag_value(argv, "--security-opt") == "no-new-privileges"
    assert _flag_value(argv, "--cap-drop") == "ALL"
    assert _flag_value(argv, "-w") == CONTAINER_WORKDIR
    assert _flag_values(argv, "-v") == [f"{worktree.resolve()}:{CONTAINER_WORKDIR}"]
    assert argv[argv.index(IMAGE) + 1 :] == list(request.argv)


def test_docker_argv_never_contains_dangerous_flags(tmp_path: pathlib.Path) -> None:
    """结构性缺席：特权位、docker socket、宿主网络在两条装配路径上都不出现。"""
    worktree = _worktree(tmp_path)
    request = _request(worktree, "python3", "-c", "print('ok')")
    runner = _docker_runner(tmp_path, network_profiles={"buildnet": ["pypi.org"]})

    corpus = [runner.build_argv(request), runner.build_argv(request, network_profile="buildnet")]
    for argv in corpus:
        joined = " ".join(argv)
        assert "--privileged" not in joined
        assert "docker.sock" not in joined
        assert "--net=host" not in joined
        assert _flag_value(argv, "--network") != "host"
        assert len(_flag_values(argv, "-v")) == 1  # 只挂本 worktree，没有第二个挂载


def test_network_profile_only_when_defined(tmp_path: pathlib.Path) -> None:
    """未定义的 profile 名不降级成 `none` 也不放行：抛稳定码异常；定义过才取它的名字。"""
    worktree = _worktree(tmp_path)
    request = _request(worktree, "python3", "-c", "print('ok')")
    runner = _docker_runner(tmp_path, network_profiles={"buildnet": ["pypi.org"]})

    with pytest.raises(CommandDeniedError) as raised:
        runner.build_argv(request, network_profile="offnet")
    assert raised.value.code == "execution.command_denied"
    assert "offnet" in raised.value.detail

    assert _flag_value(runner.build_argv(request), "--network") == "none"
    opened = runner.build_argv(request, network_profile="buildnet")
    assert _flag_value(opened, "--network") == "buildnet"


def test_profile_named_like_a_docker_escape_is_refused(tmp_path: pathlib.Path) -> None:
    """名单里真写了 `host` 也不给用：profile 名会直接进 `--network`，那是宿主机网络。"""
    worktree = _worktree(tmp_path)
    request = _request(worktree, "python3", "-c", "print('ok')")
    runner = _docker_runner(
        tmp_path, network_profiles={"host": ["x"], "none": ["y"], "bridge": ["z"]}
    )

    for name in ("host", "none", "bridge"):
        with pytest.raises(CommandDeniedError) as raised:
            runner.build_argv(request, network_profile=name)
        assert "reserved" in raised.value.detail


def test_image_must_be_pinned_not_floating(tmp_path: pathlib.Path) -> None:
    """不带 tag 等于 `:latest`：本轮拒绝它，生产默认不浮动；digest 与显式 tag 都收。"""
    worktree = _worktree(tmp_path)
    request = _request(worktree, "python3", "-c", "print('ok')")

    for floating in ("alpine", "alpine:latest", "alpine:LATEST"):
        with pytest.raises(CommandDeniedError) as raised:
            _docker_runner(tmp_path, image=floating).build_argv(request)
        assert "image" in raised.value.detail

    assert IMAGE in _docker_runner(tmp_path).build_argv(request)
    assert "alpine:3.19" in _docker_runner(tmp_path, image="alpine:3.19").build_argv(request)


def test_docker_env_flags_carry_names_not_values(tmp_path: pathlib.Path) -> None:
    """`-e` 只写键名：值留在交给 docker CLI 的那份白名单环境里，不进宿主 argv（L7）。"""
    worktree = _worktree(tmp_path)
    request = _request(worktree, "python3", "-c", "print('ok')")

    argv = _docker_runner(tmp_path).build_argv(request)

    entries = _flag_values(argv, "-e")
    assert entries == CONTAINER_KEYS
    assert not [item for item in entries if "=" in item]
    assert TICKET_ID not in " ".join(argv)  # 身份值不在装配结果里


async def test_docker_denials_reuse_the_command_boundary_and_never_spawn(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """四条判定与本地档同一份实现：被拒时 docker CLI 一次都没被碰到，模式仍写 `docker`。"""
    worktree = _worktree(tmp_path)
    fake = FakeSpawn()
    monkeypatch.setattr(docker_module.subprocess, "run", fake)
    runner = _docker_runner(tmp_path)
    corpus = [
        (_request(worktree, "python3", cwd=tmp_path / "outside"), "cwd_escape"),
        (_request(worktree), "empty_argv"),
        (_request(worktree, "rm", "-rf", "/"), "command_not_allowed"),
        (_request(worktree, "python3", "-c", "print('a;b')"), "shell_syntax"),
    ]

    for request, code in corpus:
        result = await runner.run(request)
        assert result.denied_reason == code
        assert result.mode == "docker"
        assert result.exit_code is None and result.stdout == "" and result.stderr == ""

    assert fake.calls == []


async def test_docker_results_never_impersonate_local_restricted(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """读侧唯一的判别字段：容器档的 `mode` 必须是 `docker`，summary 里不许出现本地那串。"""
    worktree = _worktree(tmp_path)
    fake = FakeSpawn(stdout=f"{STREAM_MARKER}\n", returncode=7)
    monkeypatch.setattr(docker_module.subprocess, "run", fake)
    runner = _docker_runner(tmp_path)

    result = await runner.run(_request(worktree, "python3", "-c", "print('ok')"))

    assert result.mode == "docker" and result.exit_code == 7
    assert STREAM_MARKER in result.stdout  # 正文在结果里
    assert STREAM_MARKER not in result.summary()  # 不在进 Report 的那一行里
    assert result.summary().startswith("docker exit=7")
    assert "local-restricted" not in result.summary()


async def test_run_hands_the_assembled_argv_and_the_allowlisted_env_to_docker(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """spawn 的三项事实：argv 与 `build_argv` 逐字节相同、没有 `shell`、环境只有白名单七键。"""
    worktree = _worktree(tmp_path)
    fake = FakeSpawn(stdout="ok\n")
    monkeypatch.setattr(docker_module.subprocess, "run", fake)
    runner = _docker_runner(tmp_path)
    request = _request(worktree, "python3", "-c", "print('ok')")

    result = await runner.run(request)

    argv, kwargs = fake.calls[0]
    assert fake.calls[0][0] == runner.build_argv(request)
    assert kwargs["shell"] is False and kwargs["capture_output"] is True
    assert kwargs["timeout"] == 300.0
    env = kwargs["env"]
    assert isinstance(env, dict)
    assert sorted(env) == sorted([*CONTAINER_KEYS, "PATH", "LANG"])
    assert env["STABLEAGENT_WORKTREE"] == CONTAINER_WORKDIR  # 容器里的工作目录，不是宿主路径
    assert result.stdout == "ok\n" and result.exit_code == 0


async def test_request_timeout_overrides_the_runner_default(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """容器档的默认超时更长（300 秒），请求自己填了就以请求为准——口径与本地档一致。"""
    worktree = _worktree(tmp_path)
    fake = FakeSpawn()
    monkeypatch.setattr(docker_module.subprocess, "run", fake)
    runner = _docker_runner(tmp_path)

    await runner.run(_request(worktree, "python3", "-c", "print('ok')", timeout_s=5.0))

    assert fake.calls[0][1]["timeout"] == 5.0


async def test_docker_output_is_redacted_and_capped_like_local(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """容器里的输出走同一份预算与脱敏：换执行档不该换文本口径。"""
    worktree = _worktree(tmp_path)
    fake = FakeSpawn(stdout="A" * 400 + "SEKRET" + "A" * 400, stderr="err SEKRET")
    monkeypatch.setattr(docker_module.subprocess, "run", fake)
    runner = _docker_runner(tmp_path, max_output_bytes=128, redact=("SEKRET",))

    result = await runner.run(_request(worktree, "python3", "-c", "print('ok')"))

    assert result.truncated
    assert len(result.stdout.encode("utf-8")) <= 128
    assert "SEKRET" not in result.stdout + result.stderr
    assert result.stderr == "err ***"


async def test_docker_timeout_keeps_partial_output_without_exit_code(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """客户端超时那一档和超时语义本身：没有退出码，已产出的部分要留着。"""
    worktree = _worktree(tmp_path)
    expired = subprocess.TimeoutExpired(cmd=["docker", "run"], timeout=300.0, output="partial")
    fake = FakeSpawn(raises=expired)
    monkeypatch.setattr(docker_module.subprocess, "run", fake)
    runner = _docker_runner(tmp_path)

    result = await runner.run(_request(worktree, "python3", "-c", "print('ok')"))

    assert result.timed_out and result.exit_code is None
    assert result.stdout == "partial"
    assert "timed_out" in result.summary()


def test_docker_runner_matches_the_protocol_without_inheriting_it() -> None:
    """容器档同样是 `SandboxRunner`：结构匹配、不继承，两档可互换由调用方决定（口径同本地档）。"""
    assert DockerRunner.__mro__[1] is object
    runner = DockerRunner(
        IMAGE, allowlist=frozenset(["python3"]), worktree_root=pathlib.Path("."), clock=FixedClock()
    )
    assert isinstance(runner, SandboxRunner)


def test_build_argv_refuses_the_denied_requests_too(tmp_path: pathlib.Path) -> None:
    """装配不是绕行口：直接调 `build_argv` 也走同一份 `deny_reason`，报的是同一条稳定码。"""
    worktree = _worktree(tmp_path)
    runner = _docker_runner(tmp_path)
    corpus = [
        (_request(worktree, "python3", cwd=tmp_path / "outside"), "cwd_escape"),
        (_request(worktree), "empty_argv"),
        (_request(worktree, "rm", "-rf", "/"), "command_not_allowed"),
        (_request(worktree, "python3", "-c", "print('a;b')"), "shell_syntax"),
    ]

    for request, code in corpus:
        with pytest.raises(CommandDeniedError) as raised:
            runner.build_argv(request)
        assert raised.value.code == "execution.command_denied"
        assert f"command_denied: {code}" in raised.value.detail


# ── D3（步骤 2.11）：真实 Docker 用例的双条件门禁 ─────────────────────────────
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PYPROJECT = REPO_ROOT / "pyproject.toml"
DOCKER_INTEGRATION = REPO_ROOT / "tests" / "integration" / "test_docker_runner.py"

#: marker 的注册文本；未注册的 marker 只会 warn 不会 fail，所以"注册在位"这条只能钉配置本身。
MARKER_TEXT = "docker: 需要真实 Docker 守护进程（同时设置 STABLEAGENT_DOCKER=1 才运行）"

#: 嵌套 pytest 里要剥掉的两个开关：不剥的话，开发机上导出的环境变量会让"默认跳过"这条断言
#: 变成"看机器脸色"，而这正是 L8 不能有的性质。
GATE_VARS = ("STABLEAGENT_DOCKER", "STABLEAGENT_DOCKER_IMAGE")


def _run_pytest(
    *args: str, gate: Mapping[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """在子进程里真跑一次 pytest：D3 断的是"到底是 skip 还是 fail"，只能观察一次真实收集。"""
    child = {key: value for key, value in os.environ.items() if key not in GATE_VARS}
    child.update(gate or {})
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "--no-cov", "-q", "-rs", *args],
        cwd=REPO_ROOT,
        env=child,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def test_docker_marker_is_registered() -> None:
    """marker 进 `pyproject.toml` 的 `markers`，且只写一次：文本里点名第二个条件是哪个环境变量。"""
    text = PYPROJECT.read_text(encoding="utf-8")

    assert MARKER_TEXT in text
    assert text.count(MARKER_TEXT) == 1


def test_docker_gate_needs_the_env_flag_set_to_one() -> None:
    """环境变量半边：只有 `"1"` 算开；空串、`0`、`true` 都算关——开关不是布尔解析。"""
    corpus: list[tuple[dict[str, str], bool]] = [
        ({}, False),
        ({"STABLEAGENT_DOCKER": ""}, False),
        ({"STABLEAGENT_DOCKER": "0"}, False),
        ({"STABLEAGENT_DOCKER": "true"}, False),
        ({"STABLEAGENT_DOCKER": "1"}, True),
    ]

    for env, expected in corpus:
        assert docker_module.docker_tests_enabled(env) is expected


def test_docker_cases_skip_in_the_default_environment() -> None:
    """默认环境（两个开关都不给）跑集成文件：全绿 + 跳过原因点名 `STABLEAGENT_DOCKER`。"""
    done = _run_pytest(str(DOCKER_INTEGRATION.relative_to(REPO_ROOT)))

    assert done.returncode == 0, done.stdout + done.stderr
    assert "skipped" in done.stdout.lower()
    assert "STABLEAGENT_DOCKER" in done.stdout
    assert "error" not in done.stdout.lower()


def test_docker_cases_stay_skipped_when_only_the_env_flag_is_set() -> None:
    """双条件的第二半：环境变量给了但没按 `-m docker` 选中，容器用例照样不起。"""
    done = _run_pytest(
        str(DOCKER_INTEGRATION.relative_to(REPO_ROOT)), gate={"STABLEAGENT_DOCKER": "1"}
    )

    assert done.returncode == 0, done.stdout + done.stderr
    assert "skipped" in done.stdout.lower()
    assert "-m docker" in done.stdout


def test_core_suite_does_not_depend_on_docker() -> None:
    """L8 的另一半：核心用例一条都不该被门禁跳过——它们连 Docker 都不碰，跳过就是被牵连。"""
    done = _run_pytest("tests/unit/test_command_runner.py")

    assert done.returncode == 0, done.stdout + done.stderr
    assert "passed" in done.stdout
    assert "failed" not in done.stdout
    assert "skipped" not in done.stdout.lower()


# ── D3 续：真实运行兑现的超时清理缺口（`--name` 手柄）─────────────────────────


def test_container_name_is_a_stable_digest_of_the_request(tmp_path: pathlib.Path) -> None:
    """命名是请求的纯函数：同请求两次装配同名（argv 相等性那条断言才守得住），换一样就换容器。"""
    worktree = _worktree(tmp_path)
    runner = _docker_runner(tmp_path)
    request = _request(worktree, "python3", "-c", "print('a')")

    argv = runner.build_argv(request)
    same = runner.build_argv(_request(worktree, "python3", "-c", "print('a')"))
    other_command = runner.build_argv(_request(worktree, "python3", "-c", "print('b')"))
    other_identity = runner.build_argv(
        replace(request, context=replace(request.context, ticket_id="tk-0002"))
    )

    name = _flag_value(argv, "--name")
    assert re.fullmatch(r"sa-[0-9a-f]{16}", name), name
    assert name == _flag_value(same, "--name")
    assert name != _flag_value(other_command, "--name")
    assert name != _flag_value(other_identity, "--name")
    assert name == docker_module.container_name(request)


async def test_timeout_kills_the_named_container_it_started(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Task 10 记下的缺口第一次兑现：客户端被掐之后容器还在跑——所以超时分支要补一刀定点 kill。"""
    worktree = _worktree(tmp_path)
    expired = subprocess.TimeoutExpired(cmd=["docker", "run"], timeout=0.5, output="partial")
    fake = FakeSpawn(raises=expired, raise_once=True)
    monkeypatch.setattr(docker_module.subprocess, "run", fake)
    runner = _docker_runner(tmp_path)
    request = _request(worktree, "python3", "-c", "__import__('time').sleep(30)", timeout_s=0.5)

    result = await runner.run(request)

    assert [call[0] for call in fake.calls] == [
        runner.build_argv(request),
        [docker_module.DOCKER, "kill", docker_module.container_name(request)],
    ]
    assert fake.calls[1][1]["shell"] is False
    assert fake.calls[1][1]["timeout"] == docker_module.KILL_TIMEOUT_S
    assert result.timed_out and result.exit_code is None
    assert result.stdout == "partial"


async def test_a_finished_command_is_not_killed(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """清理只跟着超时：非零退出也不补——容器自己走了，`--rm` 已经把它的文件系统带走。"""
    worktree = _worktree(tmp_path)
    fake = FakeSpawn(returncode=7, stderr="boom")
    monkeypatch.setattr(docker_module.subprocess, "run", fake)

    result = await _docker_runner(tmp_path).run(_request(worktree, "python3", "-c", "raise 'x'"))

    assert [call[0] for call in fake.calls if call[0][:2] == ["docker", "kill"]] == []
    assert len(fake.calls) == 1
    assert result.exit_code == 7 and not result.timed_out


async def test_a_failing_kill_does_not_rewrite_the_timeout(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """kill 自己也起不来时不改结论：超时这一档的事实已经定稿，清理是尽力而为。"""
    worktree = _worktree(tmp_path)
    calls: list[list[str]] = []

    def spawn(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(list(argv))
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(cmd=argv, timeout=0.5, output="partial")
        raise OSError("docker kill 起不来")

    monkeypatch.setattr(docker_module.subprocess, "run", spawn)
    runner = _docker_runner(tmp_path)

    result = await runner.run(_request(worktree, "python3", "-c", "pass", timeout_s=0.5))

    assert len(calls) == 2 and calls[1][:2] == ["docker", "kill"]
    assert result.timed_out and result.exit_code is None
    assert result.stdout == "partial"
