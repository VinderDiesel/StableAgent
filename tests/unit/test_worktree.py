"""C2~C5：路径收口、命名收口、worktree 复用与 effect 重放——执行层吃到的任何路径都在 root 内，
任何分支名只由 Ticket 决定，复用四要素不全等就拒绝并保留现场，重放不产生第二个现场。

Task 5 的清理用例继续追加在本文件。
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
from collections.abc import Callable, Sequence

import pytest

from stableagent.execution.effects import prepare_idempotency_key, prepare_worktree_handler
from stableagent.execution.errors import PathEscapeError, WorktreeReuseError
from stableagent.execution.git import (
    GitError,
    GitResult,
    SubprocessGit,
    git_env,
    scrub_credentials,
)
from stableagent.execution.naming import (
    WORKSPACE_RE,
    is_workspace_name,
    slugify,
    ticket_short_id,
    workspace_name,
)
from stableagent.execution.paths import confine
from stableagent.execution.worktree import GitWorktreeManager, WorktreeHandle
from stableagent.orchestration.model import Effect, EffectKind, EffectState, Ticket
from stableagent.orchestration.outbox import EffectDispatcher
from stableagent.orchestration.store import Store
from tests.support import FixedClock

HOSTILE_TITLES = [
    "main; rm -rf /",
    "../../etc/passwd",
    "修复登录 bug",
    "\U0001f389",
    "A" * 200,
    "  ",
    "-leading-dash",
    "ISSUE-42: Push `main` & pipe|grep $HOME",
]


def _ticket(title: str, *, ticket_id: str = "tk-0123456789abcdef0123456789abcdef") -> Ticket:
    return Ticket(
        id=ticket_id,
        mandate_id="md-naming",
        title=title,
        task="正文与命名无关",
        created_at=1_000,
    )


def test_confine_accepts_paths_inside_root(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "worktrees"
    (root / "sa-abc-def").mkdir(parents=True)
    inside = root / "sa-abc-def" / "src"
    inside.mkdir()
    assert confine(root, inside) == inside.resolve()
    assert confine(root, "sa-abc-def/src") == inside.resolve()
    assert confine(root, root) == root.resolve()  # root 自身不算逃逸


def test_confine_rejects_traversal_and_outside_paths(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "worktrees"
    root.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    for candidate in ("../elsewhere", "sa-x/../../elsewhere", str(outside), "/etc/hosts"):
        with pytest.raises(PathEscapeError):
            confine(root, candidate)


def test_confine_rejects_symlink_pointing_outside_root(tmp_path: pathlib.Path) -> None:
    """resolve() 后才比对：链接本身在 root 内不是放行理由（macOS /var → /private/var 同理）。"""
    root = tmp_path / "worktrees"
    root.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    link = root / "escape"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(PathEscapeError):
        confine(root, link)


def test_confine_error_names_the_root_and_the_candidate(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "worktrees"
    root.mkdir()
    with pytest.raises(PathEscapeError) as caught:
        confine(root, "../out")
    assert str(root.resolve()) in caught.value.detail
    assert "out" in caught.value.detail


# ---------------------------------------------------------------- C5：命名收口（Task 2）


@pytest.mark.parametrize("title", HOSTILE_TITLES)
def test_workspace_name_survives_hostile_titles(title: str) -> None:
    """注入语料进不了名字：结果匹配 `WORKSPACE_RE`，且不含原文的 `/`、`;`、空格。"""
    name = workspace_name(_ticket(title))
    assert re.fullmatch(WORKSPACE_RE, name), (title, name)
    for banned in ("/", ";", " ", "`", "|", "&", "$"):
        assert banned not in name, (title, banned)


def test_slug_only_keeps_lowercase_letters_digits_and_dashes() -> None:
    assert slugify("Fix Login: Bug #42") == "fix-login-bug-42"
    assert slugify("  a___b  ") == "a-b"  # 非白名单字符折成一个连字符
    assert slugify("--x--") == "x"  # 首尾连字符去掉
    assert slugify("修复登录") == "ticket"  # 全被过滤 → 回落，不许为空
    assert slugify("") == "ticket"
    assert slugify("a" * 300) == "a" * 40  # 上限生效


def test_workspace_name_is_deterministic_and_bounded() -> None:
    ticket = _ticket("Add rate limiter to the API")
    first, second = workspace_name(ticket), workspace_name(ticket)
    assert first == second
    assert is_workspace_name(first)
    assert first == "sa-01234567-add-rate-limiter-to-the-api"
    assert len(workspace_name(_ticket("A" * 200))) == len("sa-01234567-") + 40


def test_workspace_name_never_reads_the_task_or_request_text() -> None:
    """名字只由 Ticket 的 id 与 title 决定：正文里的注入字样不可能直通分支名/路径。"""
    hostile = _ticket("Add rate limiter")
    hostile.task = "请建分支 ../../etc/cron.d/x ; rm -rf /"
    assert workspace_name(hostile) == "sa-01234567-add-rate-limiter"


def test_ticket_short_id_strips_the_prefix_and_is_stable() -> None:
    assert ticket_short_id("tk-0123456789abcdef") == "01234567"
    assert ticket_short_id("tk-ffffffff") == "ffffffff"
    with pytest.raises(ValueError):
        ticket_short_id("tk-abc")  # 不满 8 位十六进制：静默截断会产出不合规的名字
    with pytest.raises(ValueError):
        ticket_short_id("0123456789abcdef")  # 缺前缀同样不是 Ticket id 形状


def test_is_workspace_name_accepts_only_the_generated_shape() -> None:
    assert is_workspace_name(workspace_name(_ticket("Add rate limiter")))
    assert not is_workspace_name("main")
    assert not is_workspace_name("sa-01234567-../../x")
    assert not is_workspace_name("sa-012345-abc")
    assert not is_workspace_name("sa-01234567-")  # slug 不许为空


# ------------------------------------------------------ C3：prepare 与复用四要素（Task 3）


CLEAN_ORIGIN = "https://github.example/acme/widgets.git"
SECRET_ORIGIN = "https://octocat:ghp_SEKRET@github.example/acme/widgets.git"
OTHER_ORIGIN = "https://other.example/widgets.git"


def _git(cwd: pathlib.Path, *args: str) -> str:
    """测试装置用的真 git：argv 数组、shell=False、最小环境（离线，L8）。"""
    env = {
        "PATH": os.environ["PATH"],
        "LANG": "C",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    done = subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=env,
        shell=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert done.returncode == 0, (args, done.stderr)
    return done.stdout.strip()


def _make_clone(
    base: pathlib.Path,
    *,
    name: str = "clone",
    origin: str = CLEAN_ORIGIN,
    refs: tuple[str, ...] = ("main",),
) -> pathlib.Path:
    """建一个真仓库：`refs` 上各有一个空提交，origin 指向 `origin`。"""
    clone = base / name
    clone.mkdir(parents=True)
    _git(clone, "init", "-b", refs[0])
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
    for ref in refs[1:]:
        _git(
            clone,
            "-c",
            "user.name=sa",
            "-c",
            "user.email=sa@example.invalid",
            "checkout",
            "-b",
            ref,
        )
    _git(clone, "remote", "add", "origin", origin)
    return clone


def _manager(root: pathlib.Path, clock: FixedClock) -> tuple[GitWorktreeManager, RecordingGit]:
    recorder = RecordingGit(SubprocessGit())
    return GitWorktreeManager(root, git=recorder, clock=clock), recorder


class RecordingGit:
    """包裹真 git 记录 argv：断言"参数数组、无 shell 语法"不必去扒 subprocess。"""

    def __init__(self, inner: SubprocessGit) -> None:
        self.inner = inner
        self.calls: list[tuple[tuple[str, ...], pathlib.Path]] = []

    def run(self, args: Sequence[str], *, cwd: pathlib.Path) -> GitResult:
        argv = tuple(args)
        self.calls.append((argv, cwd))
        return self.inner.run(argv, cwd=cwd)


def test_prepare_creates_worktree_branch_and_ownership(tmp_path: pathlib.Path) -> None:
    clone = _make_clone(tmp_path, refs=("main", "release-2"))
    root = tmp_path / "worktrees"
    clock = FixedClock()
    manager, recorder = _manager(root, clock)
    ticket = _ticket("Add rate limiter to the API")

    handle = manager.prepare(ticket, repository=str(clone), base_ref="release-2")

    assert handle.branch == workspace_name(ticket) == "sa-01234567-add-rate-limiter-to-the-api"
    assert handle.path == confine(root, handle.branch)
    assert handle.ticket_id == ticket.id
    assert (handle.base_ref, handle.repository) == ("release-2", CLEAN_ORIGIN)
    assert handle.ownership_file == handle.path / ".stableagent-worktree.json"
    recorded = json.loads(handle.ownership_file.read_text(encoding="utf-8"))
    assert recorded == {
        "ticket_id": ticket.id,
        "branch": handle.branch,
        "base_ref": "release-2",
        "repository": CLEAN_ORIGIN,
        "created_at": clock.now_ms(),
    }
    # 起点是 base_ref，分支是新建的受控分支
    assert _git(handle.path, "rev-parse", "--abbrev-ref", "HEAD") == handle.branch
    assert _git(handle.path, "rev-parse", "HEAD") == _git(clone, "rev-parse", "release-2")
    assert [call[0] for call in recorder.calls] == [
        ("remote", "get-url", "origin"),
        ("worktree", "add", "-b", handle.branch, str(handle.path), "release-2"),
    ]
    assert recorder.calls[0][1] == clone.resolve()


def test_prepare_reuses_the_identical_worktree(tmp_path: pathlib.Path) -> None:
    """四要素全等 → 复用：不重复 `worktree add`，ownership 的 created_at 不被改写。"""
    clone = _make_clone(tmp_path)
    manager, recorder = _manager(tmp_path / "worktrees", FixedClock())
    ticket = _ticket("Add rate limiter")
    first = manager.prepare(ticket, repository=str(clone), base_ref="main")
    adds = lambda: len([c for c in recorder.calls if c[0][0] == "worktree"])  # noqa: E731
    created = json.loads(first.ownership_file.read_text(encoding="utf-8"))["created_at"]

    second = manager.prepare(ticket, repository=str(clone), base_ref="main")

    assert (second.path, second.branch, second.ownership_file) == (
        first.path,
        first.branch,
        first.ownership_file,
    )
    assert adds() == 1, "复用不得再次 worktree add"
    assert json.loads(first.ownership_file.read_text(encoding="utf-8"))["created_at"] == created
    assert recorder.calls[-1][0] == ("remote", "get-url", "origin")  # 复用只读，不写


def _ownership_of(handle: WorktreeHandle) -> dict[str, object]:
    return json.loads(handle.ownership_file.read_text(encoding="utf-8"))


def _patch_ownership(handle: WorktreeHandle, **changes: object) -> None:
    """只改一个要素：其余字段原样保留，拒绝必须能归因到唯一的那一项。"""
    handle.ownership_file.write_text(
        json.dumps({**_ownership_of(handle), **changes}, sort_keys=True), encoding="utf-8"
    )


REUSE_BREAKS: list[tuple[str, str, Callable[[pathlib.Path, WorktreeHandle], None]]] = [
    (  # 要素①：克隆当前的 remote 与记录在案的身份不再全等
        "remote",
        "仓库 remote",
        lambda clone, handle: _git(clone, "remote", "set-url", "origin", OTHER_ORIGIN),
    ),
    (  # 要素②：ownership 指向别的 Ticket
        "ticket_id",
        "Ticket metadata",
        lambda clone, handle: _patch_ownership(handle, ticket_id="tk-ffffffff00000000"),
    ),
    (  # 要素③：base ref 变了
        "base_ref",
        "base ref",
        lambda clone, handle: _patch_ownership(handle, base_ref="dev"),
    ),
    (  # 要素④：ownership 存在但解析不了
        "ownership",
        "ownership 标记",
        lambda clone, handle: handle.ownership_file.write_text("{not json", encoding="utf-8"),
    ),
    (  # 要素④：ownership 缺失（目录不是本系统建的 worktree）
        "ownership",
        "ownership 标记",
        lambda clone, handle: handle.ownership_file.unlink(),
    ),
    (  # 要素④：ownership 的分支标记不属于这个命名口径
        "ownership",
        "ownership 标记",
        lambda clone, handle: _patch_ownership(handle, branch="main"),
    ),
]


@pytest.mark.parametrize(
    "mode,element,breaker",
    REUSE_BREAKS,
    ids=[f"{mode}-{i}" for i, (mode, _, _) in enumerate(REUSE_BREAKS)],
)
def test_reuse_rejects_each_element_and_keeps_the_scene(
    tmp_path: pathlib.Path, mode: str, element: str, breaker
) -> None:
    clone = _make_clone(tmp_path)
    root = tmp_path / "worktrees"
    manager, _ = _manager(root, FixedClock())
    ticket = _ticket("Add rate limiter")
    handle = manager.prepare(ticket, repository=str(clone), base_ref="main")
    breaker(clone, handle)

    with pytest.raises(WorktreeReuseError) as caught:
        manager.prepare(ticket, repository=str(clone), base_ref="main")

    assert caught.value.code == "execution.worktree_reuse"
    assert element in caught.value.detail
    diagnostics = caught.value.diagnostics
    assert diagnostics["element"] == mode
    assert pathlib.Path(diagnostics["path"]) == handle.path
    assert pathlib.Path(diagnostics["ownership_file"]) == handle.ownership_file
    assert set(diagnostics) == {
        "element",
        "reason",
        "path",
        "ownership_file",
        "recorded",
        "requested",
    }
    assert set(diagnostics["recorded"]) == {"remote", "ticket_id", "base_ref", "branch"}
    assert set(diagnostics["requested"]) == set(diagnostics["recorded"])
    assert handle.path.exists()  # 拒绝不等于清理：现场原样留着


def test_ownership_never_records_credentials(tmp_path: pathlib.Path) -> None:
    """L7：remote URL 里的凭据不进 ownership 文件，也不进 `WorktreeHandle.repository`。"""
    clone = _make_clone(tmp_path, origin=SECRET_ORIGIN)
    manager, _ = _manager(tmp_path / "worktrees", FixedClock())
    handle = manager.prepare(_ticket("Add rate limiter"), repository=str(clone), base_ref="main")

    assert handle.repository == CLEAN_ORIGIN
    text = handle.ownership_file.read_text(encoding="utf-8")
    for banned in ("ghp_SEKRET", "octocat", "@"):
        assert banned not in text, banned
    assert json.loads(text)["repository"] == CLEAN_ORIGIN


def test_prepare_rejects_a_repository_without_origin_remote(tmp_path: pathlib.Path) -> None:
    """读不到 remote 就不声称"仓库一致"：宁可拒绝，也不写一份无主 ownership。"""
    clone = _make_clone(tmp_path)
    _git(clone, "remote", "remove", "origin")
    manager, _ = _manager(tmp_path / "worktrees", FixedClock())

    with pytest.raises(GitError) as caught:
        manager.prepare(_ticket("Add rate limiter"), repository=str(clone), base_ref="main")
    assert caught.value.returncode != 0
    assert not (tmp_path / "worktrees").exists()  # 失败不留半成品 worktree


def test_git_carries_only_four_environment_variables() -> None:
    """本 Task 自带的取向（git 子进程最小环境，L7）：白名单外的宿主变量一个都不进。"""
    env = git_env(path="/usr/bin")
    assert set(env) == {"PATH", "LANG", "GIT_TERMINAL_PROMPT", "GIT_CONFIG_NOSYSTEM"}
    assert (env["GIT_TERMINAL_PROMPT"], env["GIT_CONFIG_NOSYSTEM"]) == ("0", "1")
    assert "GH_TOKEN" not in env and "HOME" not in env


def test_scrub_credentials_strips_userinfo_and_is_idempotent() -> None:
    assert scrub_credentials(SECRET_ORIGIN) == CLEAN_ORIGIN
    assert scrub_credentials(CLEAN_ORIGIN) == CLEAN_ORIGIN
    assert "octocat" not in scrub_credentials(SECRET_ORIGIN)
    # 路径里的 @ 不是凭据段，不许被剥掉
    assert scrub_credentials("file:///tmp/team@host/repo.git") == "file:///tmp/team@host/repo.git"


# ------------------------------------------------- C2：effect 驱动与崩溃重放（Task 4）


def _worktree_paths(clone: pathlib.Path) -> list[str]:
    lines = _git(clone, "worktree", "list", "--porcelain").splitlines()
    rows = [line for line in lines if line.startswith("worktree ")]
    return [line.removeprefix("worktree ").strip() for line in rows]


def _branch_count(clone: pathlib.Path, name: str) -> int:
    listed = _git(clone, "branch", "--list", name).strip()
    return len(listed.splitlines()) if listed else 0


def _prepare_effect(effect_id: str, ticket: Ticket, repository: str) -> Effect:
    return Effect(
        id=effect_id,
        kind=EffectKind.PREPARE_WORKTREE,
        idempotency_key=prepare_idempotency_key("as-1"),
        payload={
            "ticket_id": ticket.id,
            "assignment_id": "as-1",
            "repository": repository,
            "base_ref": "main",
        },
        due_at=1_000,
    )


async def test_prepare_effect_is_idempotent_on_replay(tmp_path: pathlib.Path) -> None:
    """C2：现场已建、账没落上——换一条连接重放，必须复用同一 worktree、账上也只有一行。"""
    clone = _make_clone(tmp_path)
    db = tmp_path / "orchestration.db"
    clock = FixedClock()
    manager, _ = _manager(tmp_path / "worktrees", clock)
    ticket = _ticket("Add rate limiter")

    store = Store(db, clock=clock)
    try:
        with store.transaction():
            store.insert_ticket(ticket)
            effect = store.enqueue_effect(_prepare_effect("ef-prep", ticket, str(clone)))
        assert effect.id == "ef-prep"
        # 崩溃点：prepare 的 git 副作用已经发生，effect 停在 running（claim 之后没落账）
        created = manager.prepare(ticket, repository=str(clone), base_ref="main")
        with store.transaction():
            store.update_effect(effect.model_copy(update={"state": EffectState.RUNNING}))
    finally:
        store.close()

    # 重放走一条全新连接：事实源是库里的行，不是上一个进程的内存（同 B4 口径）
    replay = Store(db, clock=clock)
    try:
        assert replay.reclaim_running_effects(clock.now_ms()) == 1
        dispatcher = EffectDispatcher(
            replay,
            {EffectKind.PREPARE_WORKTREE: prepare_worktree_handler(manager, replay)},
            clock=clock,
        )
        assert await dispatcher.dispatch_due() == 1

        settled = replay.get_effect("ef-prep")
        assert settled is not None and settled.state is EffectState.SUCCEEDED
        assert settled.attempts == 1  # reclaim 还掉崩溃前那次 claim，重放只算一次真实执行
        assert (
            replay._conn.execute(
                "SELECT COUNT(*) FROM effects WHERE kind = 'prepare_worktree'"
            ).fetchone()[0]
            == 1
        )
        # 现场：一个 worktree、一个分支、ownership 仍是崩溃前那一次建的
        assert _worktree_paths(clone) == [str(clone.resolve()), str(created.path)]
        assert _branch_count(clone, created.branch) == 1
        ownership = json.loads(created.ownership_file.read_text(encoding="utf-8"))
        assert ownership["created_at"] == clock.now_ms()
        assert ownership["branch"] == "sa-01234567-add-rate-limiter"
    finally:
        replay.close()


def test_prepare_effect_key_is_scoped_to_the_assignment(tmp_path: pathlib.Path) -> None:
    """幂等键 = `prepare:{assignment_id}`：同键重复入队不新增行，payload 只刷新不复活。"""
    clock = FixedClock()
    store = Store(tmp_path / "keys.db", clock=clock)
    try:
        ticket = _ticket("Add rate limiter")
        with store.transaction():
            store.insert_ticket(ticket)
            first = store.enqueue_effect(_prepare_effect("ef-1", ticket, "/clones/a"))
            second = store.enqueue_effect(_prepare_effect("ef-2", ticket, "/clones/b"))
        assert prepare_idempotency_key("as-1") == "prepare:as-1"
        assert (first.id, second.id) == ("ef-1", "ef-1")
        assert second.payload["repository"] == "/clones/b"
        assert store._conn.execute("SELECT COUNT(*) FROM effects").fetchone()[0] == 1
    finally:
        store.close()


async def test_prepare_effect_without_ticket_row_fails_loudly(tmp_path: pathlib.Path) -> None:
    """Ticket 行不在库里就炸给 dispatcher：不猜标题、不静默建 worktree。"""
    store = Store(tmp_path / "gone.db", clock=FixedClock())
    try:
        manager, _ = _manager(tmp_path / "worktrees", FixedClock())
        handler = prepare_worktree_handler(manager, store)
        effect = _prepare_effect("ef-x", _ticket("Add rate limiter"), str(_make_clone(tmp_path)))

        with pytest.raises(ValueError, match="不在库里"):
            await handler(effect)
        assert not (tmp_path / "worktrees").exists()
    finally:
        store.close()
