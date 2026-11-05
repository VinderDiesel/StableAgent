"""D3（步骤 2.11）：真实 Docker 用例的双条件门禁，与门禁放行后才成立的五条容器事实。

门禁的两半（规格 2.5 / ADR-05 决策 7、L8）：

1. `docker` marker——跑的时候要显式选中（`pytest -m docker`）；
2. `STABLEAGENT_DOCKER=1`——`execution.docker.docker_tests_enabled` 判的那一个环境变量。

两半各有落点：`pytestmark` 的 `skipif` 拦环境变量那一半，autouse fixture 拦 marker 那一半。
少写后者的话，只要谁在 shell 里导出过 `STABLEAGENT_DOCKER=1`，一次普通的全量 `pytest` 就会
起容器——"双条件"就成了一条件。第三道 `STABLEAGENT_DOCKER_IMAGE` 不是门禁而是**参数**：本文件
绝不 `docker pull`（拉取要联网，违反 L8），要跑就得指一个本地已有的、pin 住的引用。

四条容器事实是 Task 10 交下来的：那一轮只勾了参数装配（D2），"这些参数在真实容器里确实封闭"
一条都没证。所以这里的断言全部读容器里进程的实际下场，不读 argv。第五条（客户端超时后有没有
留下还在跑的容器）是那一轮记下的两个缺口之一，它同时是清理语义的用例。

**本轮未执行**：写作环境不启动容器，这几条用例从未有过一次真实运行；它们的第一次执行属于
维护者在带 Docker 的机器上的 opt-in 动作（`pytest -q -m docker`），结果回填本账本。
"""

from __future__ import annotations

import os
import pathlib
import subprocess

import pytest

from stableagent.execution.command import CommandContext, CommandRequest
from stableagent.execution.docker import CONTAINER_WORKDIR, DockerRunner, docker_tests_enabled
from tests.support import FixedClock

TICKET_ID = "tk-docker-0001"
ASSIGNMENT_ID = "as-docker-0001"
REPOSITORY = "acme/widgets"
BASE_REF = "main"

#: 宿主环境里的密钥名；容器里读到任何一个，L7 的容器半边就当场破功。
HOST_SECRETS = ("GITHUB_TOKEN", "AWS_SECRET_ACCESS_KEY", "GH_TOKEN")

#: 探针输出用 chr(1) 分段，不用换行：探针代码本身是 argv 元素，而 shell 语法字符表里有换行。
SEP = "chr(1)"

#: 容器内探针要读的变量全集 = 五个上下文键 + 三个本该缺席的宿主密钥名。
PROBE_KEYS = (
    "STABLEAGENT_TICKET_ID",
    "STABLEAGENT_ASSIGNMENT_ID",
    "STABLEAGENT_WORKTREE",
    "STABLEAGENT_REPOSITORY",
    "STABLEAGENT_BASE_REF",
    *HOST_SECRETS,
)

#: 镜像引用来自环境变量；空串时全部用例跳过（见 `_gate_conditions`）。
IMAGE = os.environ.get("STABLEAGENT_DOCKER_IMAGE", "")

pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(
        not docker_tests_enabled(),
        reason="未设置 STABLEAGENT_DOCKER=1：真实 Docker 用例跳过（CI 离线，L8）",
    ),
]


@pytest.fixture(autouse=True)
def _gate_conditions(request: pytest.FixtureRequest) -> None:
    """双条件的第二半 + 镜像参数：任何一条不满足都 skip，原因写明缺的是哪个开关。"""
    selected = request.config.getoption("markexpr") or ""
    if "docker" not in selected:
        pytest.skip("未按 -m docker 选中 docker marker：真实容器用例跳过（双条件缺一）")
    if not IMAGE:
        pytest.skip(
            "未设置 STABLEAGENT_DOCKER_IMAGE：本文件不 pull 镜像（L8），只认本地已 pin 的引用"
        )


def _worktree(tmp_path: pathlib.Path) -> pathlib.Path:
    worktree = tmp_path / "worktrees" / "sa-01234567-add-rate-limiter"
    worktree.mkdir(parents=True)
    return worktree


def _request(worktree: pathlib.Path, code: str, **changes: object) -> CommandRequest:
    context = CommandContext(
        ticket_id=TICKET_ID,
        assignment_id=ASSIGNMENT_ID,
        repository=REPOSITORY,
        base_ref=BASE_REF,
    )
    return CommandRequest(
        argv=("python3", "-c", code),
        cwd=worktree,
        context=context,
        **changes,  # type: ignore[arg-type]
    )


def _runner(tmp_path: pathlib.Path, **changes: object) -> DockerRunner:
    settings: dict[str, object] = {
        "allowlist": frozenset(["python3"]),
        "worktree_root": tmp_path / "worktrees",
        "clock": FixedClock(),
    }
    settings.update(changes)
    return DockerRunner(IMAGE, **settings)  # type: ignore[arg-type]


def _probe() -> str:
    """把要读的键拼进 `-c` 的单表达式探针：只读环境，不回显任何宿主路径。"""
    names = ", ".join(f"'{key}'" for key in PROBE_KEYS)
    return (
        "print("
        + SEP
        + ".join([k + '=' + __import__('os').environ.get(k, '<unset>') for k in ("
        + names
        + ")]))"
    )


async def test_container_runs_inside_the_mounted_worktree(tmp_path: pathlib.Path) -> None:
    """`-w /workspace` + 单一 `-v` 的下场：容器读到宿主刚写进 worktree 的文件，cwd 就是挂载点。"""
    worktree = _worktree(tmp_path)
    (worktree / "probe.txt").write_text("from-host", encoding="utf-8")
    request = _request(
        worktree, f"print(open('probe.txt').read() + {SEP} + __import__('os').getcwd())"
    )

    result = await _runner(tmp_path).run(request)

    assert result.exit_code == 0, result.stderr
    token, _, cwd = result.stdout.strip().partition("\x01")
    assert token == "from-host"
    assert cwd == CONTAINER_WORKDIR


async def test_default_profile_has_no_network_egress(tmp_path: pathlib.Path) -> None:
    """`--network none` 是真的没出口：连接尝试以 OSError 收场，一个字节都没发出去。"""
    worktree = _worktree(tmp_path)
    request = _request(
        worktree,
        "print('CONNECTED') if __import__('socket').create_connection(("
        "'1.1.1.1', 443), timeout=5) else None",
    )

    result = await _runner(tmp_path).run(request)

    assert "CONNECTED" not in result.stdout
    assert result.exit_code != 0
    assert "OSError" in result.stderr


async def test_root_filesystem_is_read_only_while_tmp_is_writable(tmp_path: pathlib.Path) -> None:
    """`--read-only` 挡住根盘写、`--tmpfs /tmp` 留出临时写：两条命令各自的退出码就是答案。"""
    worktree = _worktree(tmp_path)
    runner = _runner(tmp_path)

    blocked = await runner.run(_request(worktree, "open('/etc/stableagent-probe', 'w').write('x')"))
    allowed = await runner.run(
        _request(worktree, "print(open('/tmp/stableagent-probe', 'w').write('x'))")
    )

    assert blocked.exit_code != 0, blocked.stdout
    assert "Read-only" in blocked.stderr
    assert allowed.exit_code == 0
    assert allowed.stdout.strip() == "1"


async def test_container_environment_carries_context_and_no_host_secret(
    tmp_path: pathlib.Path,
) -> None:
    """L7 的容器半边：五个 `STABLEAGENT_*` 到位且 `WORKTREE` 说的是容器内路径，宿主密钥一个不见。"""
    worktree = _worktree(tmp_path)

    result = await _runner(tmp_path).run(_request(worktree, _probe()))

    assert result.exit_code == 0, result.stderr
    pairs = dict(line.split("=", 1) for line in result.stdout.strip().split("\x01") if "=" in line)
    assert pairs["STABLEAGENT_TICKET_ID"] == TICKET_ID
    assert pairs["STABLEAGENT_ASSIGNMENT_ID"] == ASSIGNMENT_ID
    assert pairs["STABLEAGENT_REPOSITORY"] == REPOSITORY
    assert pairs["STABLEAGENT_BASE_REF"] == BASE_REF
    assert pairs["STABLEAGENT_WORKTREE"] == CONTAINER_WORKDIR
    assert all(pairs.get(secret, "<unset>") == "<unset>" for secret in HOST_SECRETS)


async def test_client_timeout_leaves_no_running_container(tmp_path: pathlib.Path) -> None:
    """Task 10 记下的缺口之一：客户端被掐之后，容器不该继续挂在宿主上。

    本条是"补清理路径"的触发器——真实环境里如果它转红，按那一轮的约定就得在容器档加定点清理
    （`--name` 或超时后 `docker kill`），而不是把断言改松。
    """
    worktree = _worktree(tmp_path)
    runner = _runner(tmp_path, timeout_s=2.0)

    result = await runner.run(_request(worktree, "__import__('time').sleep(30)", timeout_s=0.5))

    assert result.timed_out and result.exit_code is None
    leftovers = subprocess.run(
        ["docker", "ps", "--filter", f"ancestor={IMAGE}", "--format", "{{.ID}}"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert leftovers.stdout.strip() == "", f"超时后仍在运行的容器：{leftovers.stdout}"
