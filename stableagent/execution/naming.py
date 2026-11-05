"""命名收口（C5，ADR-05 决策 6）：分支名与 worktree 目录名只由 Ticket 决定。

三个函数的签名里没有 request 文本、Issue 标题原文或工具输出——"外部文本直通分支/路径"
在本层结构上就做不到。名字形状由 `WORKSPACE_RE` 钉死：短 id 取 Ticket id 的前 8 位十六进制，
slug 只留 `[a-z0-9-]`、上限 40 字符，全被过滤掉时回落到 `ticket`（名字不许为空）。
"""

from __future__ import annotations

import re

from stableagent.orchestration.model import Ticket

SLUG_MAX_LENGTH = 40
EMPTY_SLUG = "ticket"
WORKSPACE_RE = r"^sa-[0-9a-f]{8}-[a-z0-9-]{1,40}$"

_NON_SLUG_CHARS = re.compile(r"[^a-z0-9-]+")
_REPEATED_DASHES = re.compile(r"-+")


def ticket_short_id(ticket_id: str) -> str:
    """Ticket id 的前 8 位十六进制；缺前缀或不足 8 位一律报错，不静默截出不合规的名字。"""
    if not ticket_id.startswith("tk-"):
        raise ValueError(f"Ticket id 缺少 `tk-` 前缀：{ticket_id!r}")
    body = ticket_id.removeprefix("tk-")
    if len(body) < 8:
        raise ValueError(f"Ticket id 的十六进制部分不足 8 位：{ticket_id!r}")
    return body[:8]


def slugify(title: str) -> str:
    """标题 → 受限 slug：小写、非 `[a-z0-9-]` 折成连字符、上限 40、空则回落。"""
    slug = _REPEATED_DASHES.sub("-", _NON_SLUG_CHARS.sub("-", title.lower())).strip("-")
    return slug[:SLUG_MAX_LENGTH].strip("-") or EMPTY_SLUG


def workspace_name(ticket: Ticket) -> str:
    """分支名与 worktree 目录名同值：`sa-<Ticket 短 id>-<标题 slug>`。"""
    return f"sa-{ticket_short_id(ticket.id)}-{slugify(ticket.title)}"


def is_workspace_name(value: str) -> bool:
    """ownership 文件与既有分支的复核口径：形状不合规就不认。"""
    return re.fullmatch(WORKSPACE_RE, value) is not None
