"""C4 + C5：路径收口与命名收口——执行层吃到的任何路径都在 root 内，任何分支名只由 Ticket 决定。

Task 2~5 的 prepare、复用与清理用例继续追加在本文件。
"""

from __future__ import annotations

import pathlib
import re

import pytest

from stableagent.execution.errors import PathEscapeError
from stableagent.execution.naming import (
    WORKSPACE_RE,
    is_workspace_name,
    slugify,
    ticket_short_id,
    workspace_name,
)
from stableagent.execution.paths import confine
from stableagent.orchestration.model import Ticket

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
