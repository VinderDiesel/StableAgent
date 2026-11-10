"""P7 M4：LexicalRetriever 授权过滤、过期隐藏、token 裁剪。"""

from __future__ import annotations

from pathlib import Path

from stableagent.memory.model import (
    MemoryAuthContext,
    MemoryClassification,
    MemoryLayer,
    MemoryRecord,
    MemoryScope,
    MemoryScopeKind,
    RetrievedMemory,
)
from stableagent.memory.retrieval import (
    LexicalRetriever,
    inject_into_messages,
    lexical_score,
    truncate_by_token_budget,
)
from stableagent.memory.store import MemoryStore
from tests.support import FixedClock


def _insert(
    store: MemoryStore,
    *,
    mid: str,
    content: str,
    tenant: str = "tenant-a",
    scope: MemoryScope | None = None,
    layer: MemoryLayer = MemoryLayer.LONG,
    expires_at: int | None = 100_000,
) -> None:
    scope = scope or MemoryScope(kind=MemoryScopeKind.REPOSITORY, id="repo-a")
    rec = MemoryRecord(
        id=mid,
        tenant_id=tenant,
        layer=layer,
        scope_kind=scope.kind,
        scope_id=scope.id,
        origin="verified_fact",
        content=content,
        content_digest=mid,
        created_at=1_000,
        updated_at=1_000,
        expires_at=expires_at,
        classification=MemoryClassification.INTERNAL,
    )
    with store.transaction():
        store.insert_memory(rec)


def test_lexical_score_deterministic() -> None:
    assert lexical_score(frozenset({"ruff", "mypy"}), "Prefer ruff and mypy") == 1.0
    assert lexical_score(frozenset({"ruff", "flake"}), "Prefer ruff and mypy") == 0.5


def test_auth_filters_and_skips_expired(tmp_path: Path) -> None:
    clock = FixedClock(1_000)
    store = MemoryStore(tmp_path / "m.db", clock=clock)
    _insert(store, mid="keep", content="Prefer ruff and mypy gates")
    _insert(
        store,
        mid="expired",
        content="Prefer ruff stale",
        expires_at=500,
    )
    _insert(
        store,
        mid="other",
        content="Prefer ruff other tenant",
        tenant="tenant-b",
        scope=MemoryScope(kind=MemoryScopeKind.REPOSITORY, id="repo-b"),
    )
    retriever = LexicalRetriever(store)
    auth = MemoryAuthContext(
        tenant_id="tenant-a",
        allowed_scopes=frozenset(
            {MemoryScope(kind=MemoryScopeKind.REPOSITORY, id="repo-a")}
        ),
        layers=frozenset({MemoryLayer.LONG}),
    )
    hits = retriever.retrieve("ruff mypy", auth, now_ms=1_000, min_score=0.1)
    assert [h.record.id for h in hits] == ["keep"]


def test_token_budget_truncates() -> None:
    items = [
        RetrievedMemory(
            record=MemoryRecord(
                id=f"m{i}",
                tenant_id="t",
                layer=MemoryLayer.LONG,
                scope_kind=MemoryScopeKind.TENANT,
                scope_id="t",
                origin="operator",
                content=" ".join(["word"] * 10),
                content_digest=f"d{i}",
                created_at=i,
                updated_at=i,
            ),
            score=1.0 - i * 0.1,
            token_estimate=10,
        )
        for i in range(5)
    ]
    clipped = truncate_by_token_budget(items, limit=5, token_budget=25)
    assert len(clipped) == 2
    assert sum(x.token_estimate for x in clipped) == 20


def test_inject_into_messages_empty_passthrough() -> None:
    messages = [{"role": "user", "content": "hi"}]
    assert inject_into_messages(messages, []) == messages


def test_filter_authorized_and_budget_edge(tmp_path: Path) -> None:
    from stableagent.memory.retrieval import filter_authorized

    clock = FixedClock(1_000)
    store = MemoryStore(tmp_path / "m.db", clock=clock)
    scope = MemoryScope(kind=MemoryScopeKind.REPOSITORY, id="repo-a")
    _insert(store, mid="ok", content="ruff mypy")
    _insert(
        store,
        mid="gone",
        content="ruff gone",
        expires_at=100,
    )
    auth = MemoryAuthContext(
        tenant_id="tenant-a",
        allowed_scopes=frozenset({scope}),
        layers=frozenset({MemoryLayer.LONG}),
    )
    all_rows = [
        store.get_memory("ok", tenant_id="tenant-a"),
        store.get_memory("gone", tenant_id="tenant-a"),
        MemoryRecord(
            id="leak",
            tenant_id="tenant-b",
            layer=MemoryLayer.LONG,
            scope_kind=MemoryScopeKind.REPOSITORY,
            scope_id="repo-a",
            origin="verified_fact",
            content="ruff leak",
            content_digest="leak",
            created_at=1,
            updated_at=1,
        ),
    ]
    filtered = filter_authorized(all_rows, auth, now_ms=1_000)
    assert [r.id for r in filtered] == ["ok"]

    huge = RetrievedMemory(
        record=store.get_memory("ok", tenant_id="tenant-a"),
        score=1.0,
        token_estimate=999,
    )
    assert truncate_by_token_budget([huge], limit=3, token_budget=10) == [huge]

    retriever = LexicalRetriever(store)
    assert (
        retriever.retrieve(
            "ruff",
            MemoryAuthContext(tenant_id="", allowed_scopes=frozenset({scope})),
            now_ms=1_000,
        )
        == []
    )
    low = retriever.retrieve("ruff mypy", auth, now_ms=1_000, min_score=0.99)
    assert [h.record.id for h in low] == ["ok"]
