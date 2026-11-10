"""AutoRule / AutoGate：可审计分级 auto（P7 M6 / WP-7.6）。默认关闭。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from stableagent.observability import DEFAULT_TENANT


class AutoError(Exception):
    def __init__(self, code: str, reason: str) -> None:
        self.code = code
        self.reason = reason
        super().__init__(f"{code}: {reason}")


class AutoNotFoundError(AutoError):
    def __init__(self, reason: str) -> None:
        super().__init__("auto.not_found", reason)


class AutoNotAuthorizedError(AutoError):
    def __init__(self, reason: str) -> None:
        super().__init__("auto.not_authorized", reason)


class AutoRule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    tenant_id: str = DEFAULT_TENANT
    repository: str
    enabled: bool = False
    max_actions: int = Field(default=3, ge=1)
    window_ms: int = Field(default=3_600_000, ge=1)
    allowed_reason_prefixes: tuple[str, ...] = ("inferred_candidate",)
    canary: bool = False

    @field_validator("id", "repository", "tenant_id")
    @classmethod
    def _nonempty(cls, value: str) -> str:
        if not value:
            raise ValueError("field cannot be empty")
        return value


class AutoDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    allowed: bool
    rule_id: str | None = None
    reason_codes: tuple[str, ...] = ()


class AutoGate:
    """进程内规则表：enable/disable 需 operator；evaluate 默认 deny。"""

    def __init__(self, *, authorize: Any | None = None) -> None:
        self._rules: dict[str, AutoRule] = {}
        self._accepts: dict[str, list[int]] = {}
        self._authorize = authorize or (lambda _op, _action: True)
        self.audit_log: list[dict[str, Any]] = []

    def register(self, rule: AutoRule, *, operator: str) -> None:
        if not self._authorize(operator, "register"):
            raise AutoNotAuthorizedError("register requires operator")
        self._rules[rule.id] = rule
        self._accepts.setdefault(rule.id, [])
        self._audit(operator, "register", rule.id, enabled=rule.enabled)

    def enable(self, rule_id: str, *, operator: str) -> AutoRule:
        if not self._authorize(operator, "enable"):
            raise AutoNotAuthorizedError("enable requires operator")
        rule = self._require(rule_id)
        updated = rule.model_copy(update={"enabled": True})
        self._rules[rule_id] = updated
        self._audit(operator, "enable", rule_id)
        return updated

    def disable(self, rule_id: str, *, operator: str) -> AutoRule:
        if not self._authorize(operator, "disable"):
            raise AutoNotAuthorizedError("disable requires operator")
        rule = self._require(rule_id)
        updated = rule.model_copy(update={"enabled": False})
        self._rules[rule_id] = updated
        self._audit(operator, "disable", rule_id)
        return updated

    def get(self, rule_id: str) -> AutoRule:
        return self._require(rule_id)

    def evaluate(
        self,
        *,
        tenant_id: str,
        repository: str,
        reason_codes: list[str],
        now_ms: int,
    ) -> AutoDecision:
        matched: AutoRule | None = None
        for rule in self._rules.values():
            if rule.tenant_id != tenant_id:
                continue
            if rule.repository != repository:
                continue
            matched = rule
            break
        if matched is None:
            return AutoDecision(allowed=False, reason_codes=("auto_no_rule",))
        if not matched.enabled:
            return AutoDecision(
                allowed=False, rule_id=matched.id, reason_codes=("auto_disabled",)
            )
        if not self._reasons_ok(matched, reason_codes):
            return AutoDecision(
                allowed=False,
                rule_id=matched.id,
                reason_codes=("auto_reason_mismatch",),
            )
        recent = [t for t in self._accepts.get(matched.id, []) if now_ms - t < matched.window_ms]
        self._accepts[matched.id] = recent
        if len(recent) >= matched.max_actions:
            return AutoDecision(
                allowed=False, rule_id=matched.id, reason_codes=("auto_rate_limited",)
            )
        return AutoDecision(
            allowed=True, rule_id=matched.id, reason_codes=("auto_allowed",)
        )

    def record_accept(self, rule_id: str, *, now_ms: int) -> None:
        self._accepts.setdefault(rule_id, []).append(now_ms)
        self._audit("system", "accept", rule_id, at=now_ms)

    def _reasons_ok(self, rule: AutoRule, reason_codes: list[str]) -> bool:
        if not rule.allowed_reason_prefixes:
            return True
        return any(
            any(code.startswith(prefix) for prefix in rule.allowed_reason_prefixes)
            for code in reason_codes
        )

    def _require(self, rule_id: str) -> AutoRule:
        rule = self._rules.get(rule_id)
        if rule is None:
            raise AutoNotFoundError(f"unknown auto rule: {rule_id}")
        return rule

    def _audit(self, actor: str, action: str, rule_id: str, **extra: Any) -> None:
        self.audit_log.append({"actor": actor, "action": action, "rule_id": rule_id, **extra})
