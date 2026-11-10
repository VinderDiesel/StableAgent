"""P7 M3：IdentityLink 显式链接；无隐式跨 Source 映射。"""

from __future__ import annotations

from pathlib import Path

from stableagent.supervisor.model import IdentityLink
from stableagent.supervisor.store import SupervisorStore
from tests.support import FixedClock


def test_resolve_only_via_explicit_link(tmp_path: Path) -> None:
    clock = FixedClock()
    store = SupervisorStore(tmp_path / "id.db", clock=clock)
    assert (
        store.resolve_identity(
            tenant_id="t1", source="slack", actor="U1", target_source="github"
        )
        is None
    )
    with store.transaction():
        assert store.insert_identity_link(
            IdentityLink(
                id="il-1",
                tenant_id="t1",
                left_source="slack",
                left_actor="U1",
                right_source="github",
                right_actor="alice",
                created_at=1,
                created_by="admin",
            )
        )
    assert (
        store.resolve_identity(
            tenant_id="t1", source="slack", actor="U1", target_source="github"
        )
        == "alice"
    )
    with store.transaction():
        store.revoke_identity_link("il-1", revoked_at=2, revoked_by="admin")
    assert (
        store.resolve_identity(
            tenant_id="t1", source="slack", actor="U1", target_source="github"
        )
        is None
    )
    # 其他 tenant 不可见
    assert (
        store.resolve_identity(
            tenant_id="t2", source="slack", actor="U1", target_source="github"
        )
        is None
    )
