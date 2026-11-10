"""词法检索 baseline：确定性 token overlap + token 预算裁剪。"""

from __future__ import annotations

import re
from collections.abc import Iterable

from stableagent.memory.model import (
    MemoryAuthContext,
    MemoryRecord,
    RetrievedMemory,
)
from stableagent.memory.store import MemoryStore

_TOKEN = re.compile(r"[a-z0-9_]+", re.IGNORECASE)


def tokenize(text: str) -> frozenset[str]:
    return frozenset(m.group(0).lower() for m in _TOKEN.finditer(text))


def estimate_tokens(text: str) -> int:
    # 离线近似：空白分词，至少 1。
    parts = [p for p in text.split() if p]
    return max(1, len(parts))


def lexical_score(query_tokens: frozenset[str], content: str) -> float:
    if not query_tokens:
        return 0.0
    content_tokens = tokenize(content)
    if not content_tokens:
        return 0.0
    overlap = len(query_tokens & content_tokens)
    return overlap / len(query_tokens)


def inject_into_messages(
    messages: list[dict[str, str]],
    retrieved: Iterable[RetrievedMemory],
    *,
    role: str = "system",
) -> list[dict[str, str]]:
    """将检索结果注入消息列表副本；不修改 AgentLoop / ContextAssembler 签名。"""
    snippets: list[str] = []
    for item in retrieved:
        prov = item.record.provenance or {}
        source = prov.get("source", item.record.origin)
        snippets.append(
            f"- [{item.record.id} score={item.score:.2f} src={source}] {item.record.content}"
        )
    if not snippets:
        return list(messages)
    block = "Relevant memory:\n" + "\n".join(snippets)
    return [{"role": role, "content": block}, *messages]


class LexicalRetriever:
    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    def retrieve(
        self,
        query: str,
        auth: MemoryAuthContext,
        *,
        now_ms: int,
        limit: int = 5,
        token_budget: int = 512,
        min_score: float = 0.0,
    ) -> list[RetrievedMemory]:
        if auth.tenant_id == "":
            return []
        candidates = self._store.list_candidates(auth, now_ms=now_ms)
        query_tokens = tokenize(query)
        scored: list[RetrievedMemory] = []
        for record in candidates:
            if not record.is_active(now_ms=now_ms):
                continue
            score = lexical_score(query_tokens, record.content)
            if score < min_score:
                continue
            scored.append(
                RetrievedMemory(
                    record=record,
                    score=score,
                    token_estimate=estimate_tokens(record.content),
                )
            )
        scored.sort(key=lambda item: (-item.score, -item.record.created_at, item.record.id))
        return truncate_by_token_budget(scored, limit=limit, token_budget=token_budget)


def truncate_by_token_budget(
    items: list[RetrievedMemory],
    *,
    limit: int,
    token_budget: int,
) -> list[RetrievedMemory]:
    out: list[RetrievedMemory] = []
    used = 0
    for item in items:
        if len(out) >= limit:
            break
        if used + item.token_estimate > token_budget and out:
            break
        if item.token_estimate > token_budget and not out:
            # 单条超预算：仍返回第一条的截断视图（内容不改，仅占位计入预算）
            out.append(item)
            break
        out.append(item)
        used += item.token_estimate
    return out


def filter_authorized(
    records: Iterable[MemoryRecord],
    auth: MemoryAuthContext,
    *,
    now_ms: int,
) -> list[MemoryRecord]:
    allowed = auth.allowed_scopes
    out: list[MemoryRecord] = []
    for record in records:
        if record.tenant_id != auth.tenant_id:
            continue
        if record.layer not in auth.layers:
            continue
        if record.scope not in allowed:
            continue
        if not record.is_active(now_ms=now_ms):
            continue
        out.append(record)
    return out
