"""`StaticPolicy`：静态配置驱动的受理门、Mandate 构造与授权判定（规格 2.1、ADR-05 决策 2/5）。

判定逻辑在本模块，持久事实源在 `orchestration.Store`：这里不写库、不 import 执行层与集成层，
只取 `orchestration.model` 的 `Mandate`/`Clock`/`IdFactory`（方向 control → orchestration → core）。
默认拒绝的三处落点：config 没列出的命令/MCP/网络 profile 拒、未授予的 action 拒、
`enable_auto_merge` 在构造期与运行期各拒一道（L6）。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, Field

from stableagent.control.errors import AdmissionRejectedError, RevalidationFailedError
from stableagent.control.model import (
    Action,
    Admission,
    Decision,
    ExplicitRequest,
    Resource,
)
from stableagent.orchestration.model import Clock, IdFactory, Mandate

P5_POLICY_VERSION = "p5-v1"

# 规格 2.1/2.2 的六个 revalidate 时机；接线分布在 M2（worktree）与 M3（其余五处）
REVALIDATE_TIMINGS = ("launch", "recover", "retry", "worktree", "external_write", "mcp_call")

_AUTO_MERGE = Action.ENABLE_AUTO_MERGE


def request_digest(text: str) -> str:
    """请求正文的不可变指纹：编码与转义口径与 `resource_digest` 一致。"""
    return hashlib.sha256(json.dumps(text, ensure_ascii=False).encode()).hexdigest()


class StaticPolicyConfig(BaseModel):
    """operator 一次性写定的配置。集合字段默认空，即"什么都不允许"（deny-first）。"""

    repository: str
    actors: frozenset[str] = frozenset()
    grantable_actions: frozenset[Action] = frozenset()
    allowed_commands: list[str] = Field(default_factory=list)
    allowed_mcp: list[str] = Field(default_factory=list)
    network_profiles: dict[str, list[str]] = Field(default_factory=dict)
    default_validity_ms: int = Field(ge=1)

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        # L6 的构造期一道：auto merge 不得出现在可授权集合里。绕过 `__init__` 的构造路径
        # 由 `create_mandate` 与 `authorize` 的运行期两道兜住——三处任一失手都产不出授权。
        if _AUTO_MERGE in self.grantable_actions:
            raise AdmissionRejectedError("grantable_must_exclude_auto_merge")


class StaticPolicy:
    def __init__(self, config: StaticPolicyConfig, *, clock: Clock, ids: IdFactory) -> None:
        self._config = config
        self._clock = clock
        self._ids = ids

    def admit_explicit(self, request: ExplicitRequest) -> Admission:
        """四道门：actor 白名单 → 显式 mention → 仓库精确匹配 → 生成请求摘要。

        外部文本（`request.text`）只进摘要，不参与任何判定；重复请求检测归 M3（稳定 ID 语义
        依赖 GitHub 请求模型），此处 `stable_id` 直通写进 `Admission`。
        """
        digest = request_digest(request.text)

        def outcome(admitted: bool, reason_code: str) -> Admission:
            return Admission(
                admitted=admitted,
                reason_code=reason_code,
                requester=request.requester,
                repository=request.repository,
                stable_id=request.stable_id,
                request_digest=digest,
            )

        if request.requester not in self._config.actors:
            return outcome(False, "actor_not_allowlisted")
        if not request.mentioned:
            return outcome(False, "mention_required")
        if request.repository != self._config.repository:
            return outcome(False, "repository_mismatch")
        return outcome(True, "admitted")

    def create_mandate(
        self,
        admission: Admission,
        *,
        actions: Iterable[Action | str],
        allowed_paths: Iterable[str] = (),
        allowed_commands: Iterable[str] = (),
        allowed_mcp: Iterable[str] = (),
        network_profile: str | None = None,
        base_ref: str | None = None,
        requires_approval: bool = False,
    ) -> Mandate:
        """唯一的 `p5-v1` Mandate 构造点（A2）：产出一定是 config 的子集，且不含 auto merge。"""
        if not admission.admitted:
            raise AdmissionRejectedError(f"request_not_admitted:{admission.reason_code}")
        requested = {item.value if isinstance(item, Action) else item for item in actions}
        if _AUTO_MERGE.value in requested:
            raise AdmissionRejectedError("auto_merge_not_grantable")
        grantable = {item.value for item in self._config.grantable_actions}
        self._require_subset(requested, grantable, "action_not_grantable")

        # 缺省即空集：调用方漏传一个清单，产出的是"更少授权"而不是"operator 的全部授权"
        commands = set(allowed_commands)
        self._require_subset(commands, set(self._config.allowed_commands), "command_outside_config")
        mcp = set(allowed_mcp)
        self._require_subset(mcp, set(self._config.allowed_mcp), "mcp_outside_config")
        if network_profile is not None and network_profile not in self._config.network_profiles:
            raise AdmissionRejectedError("network_profile_undefined")

        granted_at = self._clock.now_ms()
        return Mandate(
            id=self._ids.new_id("md"),
            origin_kind="github",
            requester=admission.requester,
            scope=admission.stable_id,
            policy_version=P5_POLICY_VERSION,
            granted_at=granted_at,
            repository=admission.repository,
            base_ref=base_ref,
            actions=sorted(requested - {_AUTO_MERGE.value}),
            allowed_paths=sorted(set(allowed_paths)),
            allowed_commands=sorted(commands),
            allowed_mcp=sorted(mcp),
            network_profile=network_profile,
            expires_at=granted_at + self._config.default_validity_ms,
            requires_approval=requires_approval,
            request_origin=admission.stable_id,
            request_digest=admission.request_digest,
        )

    def authorize(self, mandate: Mandate, action: Action, resource: Resource) -> Decision:
        """先判定后执行（L5）：action ∈ Mandate ∧ 资源命中 allowlist ∧ 未过期。"""
        if action is _AUTO_MERGE or action.value not in mandate.actions:
            return self._decision(mandate, False, "not_granted")
        if self._is_expired(mandate):
            return self._decision(mandate, False, "mandate_expired")
        return self._authorize_resource(mandate, resource)

    def revalidate(self, mandate: Mandate, *, when: str) -> Decision:
        """动作执行前的复检：版本漂移优先于过期（漂移意味着 Mandate 不属于本 Policy）。"""
        if when not in REVALIDATE_TIMINGS:
            raise RevalidationFailedError(when, mandate.id)
        if mandate.policy_version != P5_POLICY_VERSION:
            return self._decision(mandate, False, "policy_version_drift")
        if self._is_expired(mandate):
            return self._decision(mandate, False, "mandate_expired")
        return self._decision(mandate, True, "granted")

    def _authorize_resource(self, mandate: Mandate, resource: Resource) -> Decision:
        if resource.kind == "command":
            ok = resource.command is not None and resource.command in mandate.allowed_commands
            return self._decision(mandate, ok, "granted" if ok else "command_not_allowed")
        if resource.kind == "path":
            ok = resource.path is not None and any(
                resource.path.startswith(prefix) for prefix in mandate.allowed_paths
            )
            return self._decision(mandate, ok, "granted" if ok else "path_not_allowed")
        if resource.kind == "mcp":
            ok = (
                resource.server is not None
                and resource.tool is not None
                and f"{resource.server}:{resource.tool}" in mandate.allowed_mcp
            )
            return self._decision(mandate, ok, "granted" if ok else "mcp_not_allowed")
        if resource.kind == "network":
            origins = self._config.network_profiles.get(mandate.network_profile or "", [])
            ok = resource.url is not None and resource.url in origins
            return self._decision(mandate, ok, "granted" if ok else "network_not_allowed")
        # 其余 kind 没有 allowlist 维度：由 action 集合与有效期说话
        return self._decision(mandate, True, "granted")

    def _is_expired(self, mandate: Mandate) -> bool:
        return mandate.expires_at is not None and self._clock.now_ms() > mandate.expires_at

    @staticmethod
    def _require_subset(values: set[str], allowed: set[str], reason_code: str) -> None:
        outside = sorted(values - allowed)
        if outside:
            raise AdmissionRejectedError(f"{reason_code}:{','.join(outside)}")

    @staticmethod
    def _decision(mandate: Mandate, allowed: bool, reason: str) -> Decision:
        return Decision(allowed=allowed, reason=reason, policy_version=mandate.policy_version)
