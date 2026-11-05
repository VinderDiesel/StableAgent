"""C4：路径收口——`confine()` 之后所有执行层路径都在配置 root 内（Task 1 首例）。

Task 2~5 的命名、prepare、复用与清理用例继续追加在本文件。
"""

from __future__ import annotations

import pathlib

import pytest

from stableagent.execution.errors import PathEscapeError
from stableagent.execution.paths import confine


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
