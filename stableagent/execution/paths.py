"""路径收口的唯一实现（C4）：执行层任何吃路径的公开 API 都先过 `confine()`。

判定在解析之后：`..`、相对路径与符号链接都要先 `resolve()` 再比对，否则链接本身落在
root 内就成了放行理由。root 自身算在内部（prepare 要在 root 下建目录、也要能指认 root）。
"""

from __future__ import annotations

from pathlib import Path

from stableagent.execution.errors import PathEscapeError


def confine(root: Path, candidate: str | Path) -> Path:
    """把 `candidate` 解析成 root 子树内的绝对路径，越界则 `PathEscapeError`。"""
    resolved_root = Path(root).resolve()
    path = candidate if isinstance(candidate, Path) else Path(candidate)
    if not path.is_absolute():
        path = resolved_root / path
    resolved = path.resolve()
    if not resolved.is_relative_to(resolved_root):
        raise PathEscapeError(resolved_root, str(candidate))
    return resolved
