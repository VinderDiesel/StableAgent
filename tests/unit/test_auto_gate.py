"""P7 M6：AutoGate 默认关、频控、审计、跨租户。"""

from __future__ import annotations

import pytest

from stableagent.supervisor.auto import (
    AutoGate,
    AutoNotAuthorizedError,
    AutoRule,
)


def _rule(**kwargs: object) -> AutoRule:
    base: dict[str, object] = {
        "id": "rule-1",
        "tenant_id": "tenant-a",
        "repository": "o/r",
        "enabled": False,
        "max_actions": 2,
        "window_ms": 10_000,
    }
    base.update(kwargs)
    return AutoRule.model_validate(base)


def test_default_disabled_denies() -> None:
    gate = AutoGate()
    gate.register(_rule(), operator="admin")
    decision = gate.evaluate(
        tenant_id="tenant-a",
        repository="o/r",
        reason_codes=["inferred_candidate"],
        now_ms=1_000,
    )
    assert decision.allowed is False
    assert "auto_disabled" in decision.reason_codes


def test_enable_accept_rate_limit_and_disable_rollback() -> None:
    gate = AutoGate()
    gate.register(_rule(), operator="admin")
    gate.enable("rule-1", operator="admin")
    first = gate.evaluate(
        tenant_id="tenant-a",
        repository="o/r",
        reason_codes=["inferred_candidate"],
        now_ms=1_000,
    )
    assert first.allowed is True
    gate.record_accept("rule-1", now_ms=1_000)
    gate.record_accept("rule-1", now_ms=1_100)
    limited = gate.evaluate(
        tenant_id="tenant-a",
        repository="o/r",
        reason_codes=["inferred_candidate"],
        now_ms=1_200,
    )
    assert limited.allowed is False
    assert "auto_rate_limited" in limited.reason_codes
    gate.disable("rule-1", operator="admin")
    after = gate.evaluate(
        tenant_id="tenant-a",
        repository="o/r",
        reason_codes=["inferred_candidate"],
        now_ms=20_000,
    )
    assert after.allowed is False
    assert "auto_disabled" in after.reason_codes
    actions = [e["action"] for e in gate.audit_log]
    assert "enable" in actions and "disable" in actions and "accept" in actions


def test_cross_tenant_and_auth() -> None:
    gate = AutoGate()
    gate.register(_rule(enabled=True), operator="admin")
    denied = gate.evaluate(
        tenant_id="tenant-b",
        repository="o/r",
        reason_codes=["inferred_candidate"],
        now_ms=1,
    )
    assert denied.allowed is False
    assert "auto_no_rule" in denied.reason_codes
    locked = AutoGate(authorize=lambda _op, _a: False)
    with pytest.raises(AutoNotAuthorizedError):
        locked.register(_rule(), operator="nobody")
