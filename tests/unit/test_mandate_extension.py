"""A1（往返半边）：p5-v1 全字段落库还原、p4-local-v1 旧行读为默认、四个列表列的写入口径。"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from stableagent.orchestration.model import Mandate
from stableagent.orchestration.store import Store
from tests.support import FixedClock
from tests.unit.test_control_migration import _build_legacy_db

P5_FIELDS: dict[str, object] = {
    "repository": "acme/widgets",
    "base_ref": "main",
    "actions": ["read_repository", "run_command"],
    "allowed_paths": ["src/", "tests/"],
    "allowed_commands": ["pytest -q", "ruff check ."],
    "allowed_mcp": ["context7:docs"],
    "network_profile": "none",
    "expires_at": 1_700_000_360_000,
    "requires_approval": True,
    "request_origin": "https://github.com/acme/widgets/issues/42",
    "request_digest": "a" * 64,
}


@pytest.fixture
def store(tmp_path: Path):
    handle = Store(tmp_path / "orchestration.db", clock=FixedClock())
    yield handle
    handle.close()


def test_p5_mandate_roundtrip_keeps_every_field(store: Store) -> None:
    mandate = Mandate(
        id="md-p5",
        origin_kind="github",
        requester="octocat",
        scope="issue:42",
        policy_version="p5-v1",
        granted_at=1_000,
        **P5_FIELDS,
    )
    with store.transaction():
        store.insert_mandate(mandate)
    loaded = store.get_mandate("md-p5")
    assert loaded is not None
    assert loaded.model_dump() == mandate.model_dump()


def test_p4_local_mandate_roundtrip_unchanged(store: Store) -> None:
    """A1 往返半边：旧 6 字段构造读回逐字段相等，新字段一律取默认值。"""
    legacy = Mandate(id="md-p4", granted_at=1)
    with store.transaction():
        store.insert_mandate(legacy)
    loaded = store.get_mandate("md-p4")
    assert loaded is not None
    assert loaded.model_dump() == legacy.model_dump()
    assert (loaded.origin_kind, loaded.policy_version) == ("local", "p4-local-v1")
    assert (loaded.repository, loaded.base_ref, loaded.expires_at) == (None, None, None)
    assert loaded.requires_approval is False
    assert (loaded.actions, loaded.allowed_mcp) == ([], [])


def test_upgraded_legacy_row_reads_through_the_model_with_defaults(tmp_path: Path) -> None:
    """A1 收口：Task 2 的旧库升级路径 + 本 Task 的模型读取，旧行语义仍是 P4 本地授权。"""
    db_path = tmp_path / "p4.db"
    _build_legacy_db(db_path)
    handle = Store(db_path, clock=FixedClock())
    try:
        loaded = handle.get_mandate("md-legacy")
    finally:
        handle.close()
    assert loaded == Mandate(
        id="md-legacy",
        origin_kind="local",
        requester="local",
        scope="",
        policy_version="p4-local-v1",
        granted_at=1_000,
    )


def test_mandate_rejects_unknown_fields() -> None:
    """维护者确认（M1 出口后）：Mandate 收紧 extra="forbid"——拼错的授权字段必须炸，不能静默丢。"""
    with pytest.raises(ValidationError):
        Mandate(id="md-typo", granted_at=1, allowed_command=["pytest -q"])


def test_mandate_list_columns_are_sorted_on_write(store: Store) -> None:
    """口径钉死：四个列表列写入前规范化为升序——集合派生的授权集才能逐字节可比。"""
    mandate = Mandate(
        id="md-sort",
        granted_at=1,
        actions=["b", "a"],
        allowed_paths=["/z", "/a"],
        allowed_commands=["rm", "ls"],
        allowed_mcp=["m2:t", "m1:t"],
    )
    with store.transaction():
        store.insert_mandate(mandate)
    loaded = store.get_mandate("md-sort")
    assert loaded is not None
    assert loaded.actions == ["a", "b"]
    assert loaded.allowed_paths == ["/a", "/z"]
    assert loaded.allowed_commands == ["ls", "rm"]
    assert loaded.allowed_mcp == ["m1:t", "m2:t"]
    raw = store._conn.execute("SELECT actions FROM mandates WHERE id='md-sort'").fetchone()[0]
    assert raw == '["a", "b"]'
