"""P7 M4：离线 Memory 质量评测（召回/精度/陈旧/权限泄漏/token）。"""

from __future__ import annotations

from pathlib import Path

from stableagent.memory.eval_fixtures import (
    default_eval_cases,
    default_eval_corpus,
    precision_at_k,
    recall_at_k,
)
from stableagent.memory.model import MemoryRecord
from stableagent.memory.retrieval import LexicalRetriever, estimate_tokens
from stableagent.memory.store import MemoryStore
from tests.support import FixedClock


def _seed(store: MemoryStore, *, now_ms: int) -> None:
    for item in default_eval_corpus():
        expires_offset = item.get("expires_at_offset_ms")
        expires_at = (
            now_ms + 86_400_000
            if expires_offset is None
            else now_ms + int(expires_offset)
        )
        scope = item["scope"]
        assert not isinstance(scope, dict)
        record = MemoryRecord(
            id=str(item["id"]),
            tenant_id=str(item["tenant_id"]),
            layer=item["layer"],  # type: ignore[arg-type]
            scope_kind=scope.kind,  # type: ignore[union-attr]
            scope_id=scope.id,  # type: ignore[union-attr]
            origin=str(item["origin"]),
            content=str(item["content"]),
            content_digest=str(item["id"]),
            created_at=now_ms,
            updated_at=now_ms,
            expires_at=expires_at,
            classification=item["classification"],  # type: ignore[arg-type]
        )
        with store.transaction():
            store.insert_memory(record)


def test_offline_eval_suite(tmp_path: Path) -> None:
    clock = FixedClock(10_000)
    store = MemoryStore(tmp_path / "eval.db", clock=clock)
    _seed(store, now_ms=clock.now_ms())
    retriever = LexicalRetriever(store)
    total_tokens = 0
    for case in default_eval_cases():
        hits = retriever.retrieve(
            case.query, case.auth, now_ms=clock.now_ms(), limit=5, min_score=0.0
        )
        ids = [h.record.id for h in hits]
        for leak in case.expect_leak_ids:
            assert leak not in ids, f"{case.name} leaked {leak}"
        if case.relevant_ids:
            assert recall_at_k(ids, case.relevant_ids, 5) >= 1.0, case.name
            assert precision_at_k(ids, case.relevant_ids, 1) >= 1.0, case.name
        else:
            # 陈旧事实：相关集为空，且不应召回已过期 id
            assert "mem-stale" not in ids
        total_tokens += sum(h.token_estimate for h in hits)
    # token 成本上界：评测全套检索合计不应失控
    assert total_tokens < 200
    assert estimate_tokens("one two three") == 3
