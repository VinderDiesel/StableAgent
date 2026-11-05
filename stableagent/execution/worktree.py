"""worktree 的创建与复用（规格 2.3，ADR-05 决策 6）。

`prepare(ticket, repository=..., base_ref=...)` 的 `repository` 参数是**本地克隆的根目录**；
写进 ownership 与 `WorktreeHandle` 的 `repository` 字段是该克隆的 remote 身份——已由传输层剥掉
凭据，所以 ownership 里不可能带出 token（L7）。

复用判定四要素全等才复用：① 仓库 remote（记录在案 vs 克隆当前）② Ticket metadata
③ base ref ④ ownership 标记 `.stableagent-worktree.json`——存在、可解析、且其分支标记指向本
Ticket。任一不符 → `WorktreeReuseError` 带逐项诊断，**且不清理现场**：Ticket 结束后 worktree
默认保留供审查，删除只属于显式 cleanup（Task 5）。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from stableagent.execution.errors import WorktreeReuseError
from stableagent.execution.git import Git
from stableagent.execution.naming import workspace_name
from stableagent.execution.paths import confine
from stableagent.orchestration.model import Clock, Ticket

OWNERSHIP_FILENAME = ".stableagent-worktree.json"

_UNREADABLE = "<ownership 不可读>"
_ABSENT = "<字段缺失>"

#: (要素键, recorded 键, ownership 文件里的键, 人读标签)——四要素逐项比较的口径来源。
_ELEMENTS: tuple[tuple[str, str, str, str], ...] = (
    ("remote", "remote", "repository", "仓库 remote"),
    ("ticket_id", "ticket_id", "ticket_id", "Ticket metadata"),
    ("base_ref", "base_ref", "base_ref", "base ref"),
    ("ownership", "branch", "branch", "ownership 标记"),
)

_RECORDED_KEYS = tuple(element[1] for element in _ELEMENTS)


@dataclass(frozen=True)
class WorktreeHandle:
    """一次 prepare 的结果：路径、受控分支名、归属与 ownership 文件位置。"""

    path: Path
    branch: str
    ticket_id: str
    base_ref: str
    repository: str
    ownership_file: Path


class GitWorktreeManager:
    """按 Ticket 建 worktree；git 经注入的 `Git` 协议调用（M2 取向 ①）。"""

    def __init__(self, root: Path, *, git: Git, clock: Clock) -> None:
        self._root = Path(root)
        self._git = git
        self._clock = clock

    def prepare(self, ticket: Ticket, *, repository: str, base_ref: str) -> WorktreeHandle:
        clone = Path(repository).resolve()
        remote = self._remote(clone)
        branch = workspace_name(ticket)
        path = confine(self._root, branch)
        ownership_file = path / OWNERSHIP_FILENAME
        handle = WorktreeHandle(
            path=path,
            branch=branch,
            ticket_id=ticket.id,
            base_ref=base_ref,
            repository=remote,
            ownership_file=ownership_file,
        )
        if path.exists():
            self._assert_reusable(handle, ticket=ticket, remote=remote, base_ref=base_ref)
            return handle

        path.parent.mkdir(parents=True, exist_ok=True)
        self._git.run(["worktree", "add", "-b", branch, str(path), base_ref], cwd=clone)
        self._write_ownership(handle)
        return handle

    def _remote(self, clone: Path) -> str:
        """克隆的 origin 身份（脱敏后）；读不到就拒绝 prepare，不写无主 ownership。"""
        return self._git.run(["remote", "get-url", "origin"], cwd=clone).stdout.strip()

    def _write_ownership(self, handle: WorktreeHandle) -> None:
        payload = {
            "ticket_id": handle.ticket_id,
            "branch": handle.branch,
            "base_ref": handle.base_ref,
            "repository": handle.repository,
            "created_at": self._clock.now_ms(),
        }
        handle.ownership_file.write_text(
            json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )

    def _assert_reusable(
        self, handle: WorktreeHandle, *, ticket: Ticket, remote: str, base_ref: str
    ) -> None:
        requested = {
            "remote": remote,
            "ticket_id": ticket.id,
            "base_ref": base_ref,
            "branch": handle.branch,
        }
        data = self._load_ownership(handle.ownership_file)
        if data is None:
            # 要素④先于其余三项：ownership 读不出时，"remote 不符"之类的话没有依据。
            raise WorktreeReuseError(
                "ownership 标记缺失或无法解析：无法证明这个目录属于本次请求",
                {
                    "element": "ownership",
                    "reason": "ownership 标记",
                    "path": str(handle.path),
                    "ownership_file": str(handle.ownership_file),
                    "recorded": dict.fromkeys(_RECORDED_KEYS, _UNREADABLE),
                    "requested": requested,
                },
            )
        recorded = {key: data.get(own_key, _ABSENT) for _, key, own_key, _ in _ELEMENTS}
        for element, recorded_key, _, label in _ELEMENTS:
            if recorded[recorded_key] == requested[recorded_key]:
                continue
            was, want = recorded[recorded_key], requested[recorded_key]
            raise WorktreeReuseError(
                f"{label} 不符：记录 {was!r}，本次要求 {want!r}",
                {
                    "element": element,
                    "reason": label,
                    "path": str(handle.path),
                    "ownership_file": str(handle.ownership_file),
                    "recorded": recorded,
                    "requested": requested,
                },
            )

    def _load_ownership(self, ownership_file: Path) -> Mapping[str, Any] | None:
        """ownership 文件 → 字典；缺失、读失败或不是对象都返回 None（按要素④拒绝）。"""
        if not ownership_file.is_file():
            return None
        try:
            data = json.loads(ownership_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, Mapping) else None
