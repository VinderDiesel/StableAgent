"""P7 M5：tenant 路径与断言。"""

from __future__ import annotations

from pathlib import Path

import pytest

from stableagent.control.tenancy import (
    TenantMismatchError,
    TenantPathError,
    assert_same_tenant,
    tenant_path,
)


def test_assert_same_tenant() -> None:
    assert_same_tenant("a", "a")
    with pytest.raises(TenantMismatchError):
        assert_same_tenant("a", "b")


def test_tenant_path_rejects_escape(tmp_path: Path) -> None:
    root = tmp_path / "state"
    root.mkdir()
    ok = tenant_path(root, "tenant-a", "worktrees", "t1")
    assert ok.parts[-3:] == ("tenant-a", "worktrees", "t1")
    with pytest.raises(TenantPathError):
        tenant_path(root, "tenant-a", "../tenant-b/secret")
    with pytest.raises(TenantPathError):
        tenant_path(root, "tenant-a", "/etc/passwd")
    with pytest.raises(ValueError):
        tenant_path(root, "../bad", "x")
