"""离线 Memory 质量评测集：召回、精度、陈旧、权限泄漏、token 成本。"""

from __future__ import annotations

from dataclasses import dataclass

from stableagent.memory.model import (
    MemoryAuthContext,
    MemoryClassification,
    MemoryLayer,
    MemoryScope,
    MemoryScopeKind,
)


@dataclass(frozen=True)
class EvalCase:
    name: str
    query: str
    relevant_ids: frozenset[str]
    auth: MemoryAuthContext
    expect_leak_ids: frozenset[str] = frozenset()


def default_eval_corpus() -> list[dict[str, object]]:
    """确定性语料：id / tenant / layer / scope / content / classification。"""
    return [
        {
            "id": "mem-py-style",
            "tenant_id": "tenant-a",
            "layer": MemoryLayer.LONG,
            "scope": MemoryScope(kind=MemoryScopeKind.REPOSITORY, id="repo-a"),
            "content": "Prefer ruff and mypy for python style gates",
            "origin": "verified_fact",
            "classification": MemoryClassification.INTERNAL,
        },
        {
            "id": "mem-outbox",
            "tenant_id": "tenant-a",
            "layer": MemoryLayer.LONG,
            "scope": MemoryScope(kind=MemoryScopeKind.REPOSITORY, id="repo-a"),
            "content": "Durable outbox must keep effect idempotency keys",
            "origin": "verified_fact",
            "classification": MemoryClassification.INTERNAL,
        },
        {
            "id": "mem-stale",
            "tenant_id": "tenant-a",
            "layer": MemoryLayer.LONG,
            "scope": MemoryScope(kind=MemoryScopeKind.REPOSITORY, id="repo-a"),
            "content": "Old fact: python style uses only flake8",
            "origin": "verified_fact",
            "classification": MemoryClassification.INTERNAL,
            "expires_at_offset_ms": -1,
        },
        {
            "id": "mem-ticket",
            "tenant_id": "tenant-a",
            "layer": MemoryLayer.SHORT,
            "scope": MemoryScope(kind=MemoryScopeKind.TICKET, id="tk-1"),
            "content": "Ticket prefers quiet notifications for python review",
            "origin": "worker_summary",
            "classification": MemoryClassification.INTERNAL,
        },
        {
            "id": "mem-other-tenant",
            "tenant_id": "tenant-b",
            "layer": MemoryLayer.LONG,
            "scope": MemoryScope(kind=MemoryScopeKind.REPOSITORY, id="repo-b"),
            "content": "Prefer ruff and mypy for python style gates in tenant b",
            "origin": "verified_fact",
            "classification": MemoryClassification.INTERNAL,
        },
    ]


def default_eval_cases() -> list[EvalCase]:
    repo_scope = MemoryScope(kind=MemoryScopeKind.REPOSITORY, id="repo-a")
    ticket_scope = MemoryScope(kind=MemoryScopeKind.TICKET, id="tk-1")
    return [
        EvalCase(
            name="recall_python_style",
            query="python style ruff mypy",
            relevant_ids=frozenset({"mem-py-style"}),
            auth=MemoryAuthContext(
                tenant_id="tenant-a",
                allowed_scopes=frozenset({repo_scope}),
                layers=frozenset({MemoryLayer.LONG}),
            ),
            expect_leak_ids=frozenset({"mem-other-tenant"}),
        ),
        EvalCase(
            name="precision_outbox",
            query="outbox idempotency effect",
            relevant_ids=frozenset({"mem-outbox"}),
            auth=MemoryAuthContext(
                tenant_id="tenant-a",
                allowed_scopes=frozenset({repo_scope}),
                layers=frozenset({MemoryLayer.LONG}),
            ),
        ),
        EvalCase(
            name="no_stale_fact",
            query="python style flake8",
            relevant_ids=frozenset(),
            auth=MemoryAuthContext(
                tenant_id="tenant-a",
                allowed_scopes=frozenset({repo_scope}),
                layers=frozenset({MemoryLayer.LONG}),
            ),
        ),
        EvalCase(
            name="scope_ticket_only",
            query="python review notifications",
            relevant_ids=frozenset({"mem-ticket"}),
            auth=MemoryAuthContext(
                tenant_id="tenant-a",
                allowed_scopes=frozenset({ticket_scope}),
                layers=frozenset({MemoryLayer.SHORT}),
            ),
            expect_leak_ids=frozenset({"mem-py-style", "mem-other-tenant"}),
        ),
    ]


def precision_at_k(retrieved_ids: list[str], relevant: frozenset[str], k: int) -> float:
    top = retrieved_ids[:k]
    if not top:
        return 0.0
    hits = sum(1 for item in top if item in relevant)
    return hits / len(top)


def recall_at_k(retrieved_ids: list[str], relevant: frozenset[str], k: int) -> float:
    if not relevant:
        return 1.0
    top = set(retrieved_ids[:k])
    return len(top & relevant) / len(relevant)
