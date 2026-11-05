"""C1：同一仓库同一 base ref 上并行开两个 Ticket，目录、分支、ownership 与工作区内容互不污染。

这一档只说"并行"这一件事，其余口径都在 `tests/unit/test_worktree.py`：命名形状、复用四要素、
清理确认、effect 重放。这里要证的是一条单线程用例永远证不到的性质——**两个 Ticket 同时进来**
时会不会共享状态：同一个主克隆上 `git worktree add` 抢不抢锁、`root` 目录的 `mkdir` 撞不撞、
两份 ownership 会不会互相覆盖。

三处装置写在名字旁边：

* **真 git、真目录**（M2 取向 ①）：临时目录里 `git init` 出一个带空提交的克隆，origin 是
  `https://github.example/…` 这样的假 URL——`worktree add` 只读本地对象库，一次也不会去连它，
  所以本文件离线（L8）。
* **并发怎么造**：`GitWorktreeManager.prepare` 是同步的（它是 effect handler 的下游，不在
  `SandboxRunner` 协议上），并发用 `asyncio.to_thread` + `gather`；命令那半边直接走
  `LocalRestrictedRunner`——它自己就把 spawn 下沉到线程，两次 `run` 并发是真并发。
* **子进程探针写成单表达式**：`argv` 要过 `SHELL_METACHARACTERS` 判定，`;` `$` 反引号 换行
  一个都不许出现，测试自己也在这条规则之下（同 Task 9 的口径）。

`origin` 只用于 ownership 的身份比较，凭据敏感性在 `test_worktree.py` 里证；本文件不重复。
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import re
import subprocess
import sys
from collections.abc import Sequence

from stableagent.execution.command import CommandContext, CommandRequest, CommandRunner
from stableagent.execution.git import SubprocessGit
from stableagent.execution.naming import WORKSPACE_RE
from stableagent.execution.sandbox import LocalRestrictedRunner
from stableagent.execution.worktree import OWNERSHIP_FILENAME, GitWorktreeManager, WorktreeHandle
from stableagent.orchestration.model import Ticket
from tests.support import FixedClock

ORIGIN = "https://github.example/acme/widgets.git"
BASE_REF = "main"
TICKET_A = "tk-aaaaaaaa000000000000000000000000"
TICKET_B = "tk-bbbbbbbb000000000000000000000000"

#: 打印自己的 cwd，并把 Ticket id 写进当前目录的 `who.txt`——写在哪、没写在哪，就是"互不污染"
#: 在文件系统上的形状；两样都从 stdout 里读，探针不给 shell 任何可解释的东西。
PROBE = (
    "print(__import__('os').getcwd(), "
    "open('who.txt', 'w').write(__import__('os').environ['STABLEAGENT_TICKET_ID']))"
)


def _git(cwd: pathlib.Path, *args: str) -> str:
    """测试装置用的真 git：argv 数组、shell=False、最小环境（离线，L8）。"""
    done = subprocess.run(
        ["git", *args],
        cwd=cwd,
        env={
            "PATH": os.environ["PATH"],
            "LANG": "C",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
        },
        shell=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert done.returncode == 0, (args, done.stderr)
    return done.stdout.strip()


def _make_clone(base: pathlib.Path) -> pathlib.Path:
    """真仓库：`main` 上一个空提交，origin 指向假 URL。"""
    clone = base / "clone"
    clone.mkdir(parents=True)
    _git(clone, "init", "-b", BASE_REF)
    _git(
        clone,
        "-c",
        "user.name=sa",
        "-c",
        "user.email=sa@example.invalid",
        "commit",
        "--allow-empty",
        "-m",
        "seed",
    )
    _git(clone, "remote", "add", "origin", ORIGIN)
    return clone


def _ticket(ticket_id: str, title: str) -> Ticket:
    return Ticket(
        id=ticket_id,
        mandate_id="md-concurrency",
        title=title,
        task="正文与并发无关",
        created_at=1_000,
    )


def _pair() -> tuple[Ticket, Ticket]:
    return (_ticket(TICKET_A, "add rate limiter"), _ticket(TICKET_B, "fix login bug"))


def _manager(root: pathlib.Path) -> GitWorktreeManager:
    return GitWorktreeManager(root, git=SubprocessGit(), clock=FixedClock())


async def _prepare_concurrently(
    manager: GitWorktreeManager, clone: pathlib.Path, tickets: Sequence[Ticket]
) -> list[WorktreeHandle]:
    """同一主克隆、同一 base ref，N 个 Ticket 一起进 prepare。"""
    return list(
        await asyncio.gather(
            *(
                asyncio.to_thread(manager.prepare, ticket, repository=str(clone), base_ref=BASE_REF)
                for ticket in tickets
            )
        )
    )


def _worktree_rows(clone: pathlib.Path) -> list[str]:
    """`git worktree list --porcelain` 里除主克隆之外的行——主克隆自己也占一行，所以
    "Ticket 数 == 行数"必须先去掉它。"""
    main = str(clone.resolve())
    return [
        line.removeprefix("worktree ").strip()
        for line in _git(clone, "worktree", "list", "--porcelain").splitlines()
        if line.startswith("worktree ") and line.removeprefix("worktree ").strip() != main
    ]


def _branch_lines(clone: pathlib.Path, name: str) -> int:
    listed = _git(clone, "branch", "--list", name)
    return len(listed.splitlines()) if listed else 0


async def test_parallel_tickets_get_disjoint_worktrees(tmp_path: pathlib.Path) -> None:
    """两个 Ticket 并发 prepare：目录名与分支名各自不同，git 那边恰好两个 worktree，
    两份 ownership 各说各话，谁也没覆盖谁。"""
    clone = _make_clone(tmp_path)
    root = tmp_path / "worktrees"
    manager = _manager(root)
    ticket_a, ticket_b = _pair()

    first, second = await _prepare_concurrently(manager, clone, (ticket_a, ticket_b))

    assert first.path != second.path
    assert first.branch != second.branch
    assert re.fullmatch(WORKSPACE_RE, first.branch), first.branch
    assert re.fullmatch(WORKSPACE_RE, second.branch), second.branch
    assert sorted([str(first.path), str(second.path)]) == sorted(_worktree_rows(clone))
    assert (_branch_lines(clone, first.branch), _branch_lines(clone, second.branch)) == (1, 1)

    for handle, ticket in ((first, ticket_a), (second, ticket_b)):
        payload = json.loads(handle.ownership_file.read_text(encoding="utf-8"))
        assert payload["ticket_id"] == ticket.id, payload
        assert payload["branch"] == handle.branch, payload
        assert payload["base_ref"] == BASE_REF
        assert payload["repository"] == ORIGIN
        assert handle.ticket_id == ticket.id
        assert handle.repository == ORIGIN
        assert handle.clone == clone.resolve()
        assert handle.ownership_file.parent == handle.path
    assert first.ownership_file != second.ownership_file


async def test_parallel_commands_run_in_their_own_worktree(tmp_path: pathlib.Path) -> None:
    """两条命令并发跑：stdout 里的 cwd 就是各自的 worktree，`who.txt` 只落在自己那份里。

    `STABLEAGENT_TICKET_ID` 是执行环境里唯一带着 Ticket 身份的键（L7 的白名单），孩子把它写进
    自己的 cwd——于是"串台"会在文件系统上留下痕迹，不用猜。
    """
    clone = _make_clone(tmp_path)
    root = tmp_path / "worktrees"
    manager = _manager(root)
    ticket_a, ticket_b = _pair()
    first, second = await _prepare_concurrently(manager, clone, (ticket_a, ticket_b))
    runner = LocalRestrictedRunner(
        CommandRunner(frozenset({sys.executable}), worktree_root=root, clock=FixedClock())
    )

    def _request(handle: WorktreeHandle, ticket: Ticket) -> CommandRequest:
        return CommandRequest(
            argv=(sys.executable, "-c", PROBE),
            cwd=handle.path,
            context=CommandContext(
                ticket_id=ticket.id,
                assignment_id=f"as-{ticket.id[3:11]}",
                repository=ORIGIN,
                base_ref=BASE_REF,
            ),
        )

    result_a, result_b = await asyncio.gather(
        runner.run(_request(first, ticket_a)), runner.run(_request(second, ticket_b))
    )

    assert (result_a.exit_code, result_b.exit_code) == (0, 0), (result_a.stderr, result_b.stderr)
    for result, handle, ticket in ((result_a, first, ticket_a), (result_b, second, ticket_b)):
        cwd, written = result.stdout.strip().rsplit(" ", 1)
        assert cwd == str(handle.path), (cwd, handle.path)
        assert int(written) == len(ticket.id)
        assert (handle.path / "who.txt").read_text(encoding="utf-8") == ticket.id
    assert (first.path / "who.txt").read_text(encoding="utf-8") != (
        second.path / "who.txt"
    ).read_text(encoding="utf-8")


async def test_uncommitted_files_do_not_cross_the_worktrees(tmp_path: pathlib.Path) -> None:
    """工作区侧的第三半边：A 里落一个未跟踪文件，`git status` 只在 A 里看见它，B 那边只剩
    自己的 ownership 标记，主克隆仍然干净。

    这一条不并发也成立，但它把"互不污染"从"目录名不同"推到"两个工作区各自有 index"——
    共享 index 的话，A 的未跟踪文件会出现在 B 的 `status` 里。B 的输出不许是空：
    `.stableagent-worktree.json` 是本系统自己写进工作区的唯一未跟踪文件（`worktree.py` 的清理
    口径就建在这句话上），所以"干净"在这里等于"只有那一个名字"。
    """
    clone = _make_clone(tmp_path)
    root = tmp_path / "worktrees"
    manager = _manager(root)
    ticket_a, ticket_b = _pair()
    first, second = await _prepare_concurrently(manager, clone, (ticket_a, ticket_b))

    (first.path / "only-a.txt").write_text("A\n", encoding="utf-8")

    assert "only-a.txt" in _git(first.path, "status", "--porcelain", "--untracked-files=all")
    assert _git(second.path, "status", "--porcelain", "--untracked-files=all").splitlines() == [
        f"?? {OWNERSHIP_FILENAME}"
    ]
    assert _git(clone, "status", "--porcelain", "--untracked-files=all") == ""
    assert not (second.path / "only-a.txt").exists()


async def test_three_tickets_in_parallel_stay_disjoint(tmp_path: pathlib.Path) -> None:
    """并发度不是 2 的专利：三个 Ticket 一起进来仍然是三个目录、三条分支、三份 ownership。

    这条排除"只有两个才碰巧不打架"的解释。
    """
    clone = _make_clone(tmp_path)
    root = tmp_path / "worktrees"
    manager = _manager(root)
    tickets = [
        _ticket(f"tk-{index:08x}000000000000000000000000", f"task number {index}")
        for index in range(1, 4)
    ]

    handles = await _prepare_concurrently(manager, clone, tickets)

    assert len({handle.path for handle in handles}) == 3
    assert len({handle.branch for handle in handles}) == 3
    assert len(_worktree_rows(clone)) == 3
    assert all(
        json.loads(handle.ownership_file.read_text(encoding="utf-8"))["ticket_id"] == ticket.id
        for handle, ticket in zip(handles, tickets, strict=True)
    )
