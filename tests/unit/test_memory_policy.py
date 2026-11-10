"""P7 M4：MemoryPolicy origin / 分类 / 去重 / 容量。"""

from __future__ import annotations

import pytest

from stableagent.memory.errors import MemoryPolicyDeniedError
from stableagent.memory.model import (
    MemoryClassification,
    MemoryLayer,
    MemoryRecord,
    MemoryScopeKind,
)
from stableagent.memory.policy import MemoryPolicy


def _dup() -> MemoryRecord:
    return MemoryRecord(
        id="mem-x",
        tenant_id="t",
        layer=MemoryLayer.SHORT,
        scope_kind=MemoryScopeKind.TICKET,
        scope_id="tk",
        origin="worker_summary",
        content="x",
        content_digest="a" * 64,
        created_at=1,
        updated_at=1,
    )


def test_rejects_disallowed_origin() -> None:
    policy = MemoryPolicy(allowed_origins=frozenset({"operator"}))
    with pytest.raises(MemoryPolicyDeniedError, match="origin"):
        policy.assert_writable(
            origin="worker_summary",
            classification=MemoryClassification.INTERNAL,
            content="ok",
            existing_count=0,
            duplicate=None,
        )


def test_rejects_secret_and_over_max_classification() -> None:
    policy = MemoryPolicy(max_classification=MemoryClassification.INTERNAL)
    with pytest.raises(MemoryPolicyDeniedError, match="secret"):
        policy.assert_writable(
            origin="operator",
            classification=MemoryClassification.SECRET,
            content="secret payload",
            existing_count=0,
            duplicate=None,
        )
    with pytest.raises(MemoryPolicyDeniedError, match="classification"):
        policy.assert_writable(
            origin="operator",
            classification=MemoryClassification.CONFIDENTIAL,
            content="too high",
            existing_count=0,
            duplicate=None,
        )


def test_rejects_duplicate_and_capacity() -> None:
    policy = MemoryPolicy(max_items_per_scope=1)
    with pytest.raises(MemoryPolicyDeniedError, match="duplicate"):
        policy.assert_writable(
            origin="worker_summary",
            classification=MemoryClassification.INTERNAL,
            content="same",
            existing_count=0,
            duplicate=_dup(),
        )
    with pytest.raises(MemoryPolicyDeniedError, match="capacity"):
        policy.assert_writable(
            origin="worker_summary",
            classification=MemoryClassification.INTERNAL,
            content="new",
            existing_count=1,
            duplicate=None,
        )


def test_sanitize_truncates_and_ttl() -> None:
    policy = MemoryPolicy(max_content_chars=10, short_ttl_ms=100, long_ttl_ms=None)
    cleaned = policy.assert_writable(
        origin="worker_summary",
        classification=MemoryClassification.INTERNAL,
        content="0123456789abcdef",
        existing_count=0,
        duplicate=None,
    )
    assert cleaned.endswith("…")
    assert len(cleaned) == 10
    assert policy.resolve_expires_at(MemoryLayer.SHORT, now_ms=50) == 150
    assert policy.resolve_expires_at(MemoryLayer.LONG, now_ms=50) is None
    assert policy.resolve_expires_at(MemoryLayer.LONG, now_ms=50) is None
    policy2 = MemoryPolicy(long_ttl_ms=200)
    assert policy2.resolve_expires_at(MemoryLayer.LONG, now_ms=50) == 250


def test_empty_content_and_helpers() -> None:
    from stableagent.memory.model import MemoryScope, MemoryScopeKind
    from stableagent.memory.policy import provenance_dict, scope_key

    policy = MemoryPolicy()
    with pytest.raises(MemoryPolicyDeniedError, match="empty"):
        policy.assert_writable(
            origin="worker_summary",
            classification=MemoryClassification.INTERNAL,
            content="   ",
            existing_count=0,
            duplicate=None,
        )
    scope = MemoryScope(kind=MemoryScopeKind.TICKET, id="tk-1")
    assert scope_key(scope) == ("ticket", "tk-1")
    assert provenance_dict(source="x") == {"source": "x"}
    # deleted duplicate 不挡写入
    dup = _dup().model_copy(update={"deleted_at": 9})
    assert (
        policy.assert_writable(
            origin="worker_summary",
            classification=MemoryClassification.INTERNAL,
            content="revived",
            existing_count=0,
            duplicate=dup,
        )
        == "revived"
    )
