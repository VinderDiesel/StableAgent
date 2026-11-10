"""ProviderRouter：Assignment 启动前确定性选择；禁止 step 内静默换模型。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ProviderName = Literal["openai", "anthropic", "ollama"]


class ProviderCandidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: ProviderName
    model: str
    capabilities: frozenset[str] = Field(default_factory=frozenset)
    healthy: bool = True
    unit_cost_hint: float = 0.0


class RoutingRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str = "default"
    task_class: str = "general"
    required_capabilities: frozenset[str] = Field(default_factory=frozenset)
    prefer_cheap: bool = True
    price_table_version: str = "v0"


class ProviderSelection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: ProviderName
    model: str
    reason_codes: list[str]
    price_table_version: str
    candidates: list[dict[str, Any]]
    fallback_chain: list[dict[str, str]]
    tenant_id: str
    task_class: str


class ProviderRouter:
    """纯函数式选择：同输入同输出，便于重放解释。"""

    def __init__(self, candidates: list[ProviderCandidate]) -> None:
        self._candidates = list(candidates)

    def select(self, request: RoutingRequest) -> ProviderSelection:
        eligible: list[ProviderCandidate] = []
        for candidate in self._candidates:
            if not candidate.healthy:
                continue
            if request.required_capabilities and not request.required_capabilities.issubset(
                candidate.capabilities
            ):
                continue
            eligible.append(candidate)
        if not eligible:
            raise LookupError("no healthy provider candidate matches capabilities")

        reasons = ["capability_match", "healthy"]
        ordered = list(eligible)
        if request.prefer_cheap:
            ordered.sort(key=lambda c: (c.unit_cost_hint, c.provider, c.model))
            reasons.append("prefer_cheap")
        else:
            ordered.sort(key=lambda c: (c.provider, c.model))
            reasons.append("stable_order")

        chosen = ordered[0]
        fallback = [{"provider": c.provider, "model": c.model} for c in ordered[1:]]
        return ProviderSelection(
            provider=chosen.provider,
            model=chosen.model,
            reason_codes=reasons,
            price_table_version=request.price_table_version,
            candidates=[
                {
                    "provider": c.provider,
                    "model": c.model,
                    "healthy": c.healthy,
                    "unit_cost_hint": c.unit_cost_hint,
                    "capabilities": sorted(c.capabilities),
                }
                for c in self._candidates
            ],
            fallback_chain=fallback,
            tenant_id=request.tenant_id,
            task_class=request.task_class,
        )


def merge_provider_selection(
    worker_config: dict[str, Any], selection: ProviderSelection
) -> dict[str, Any]:
    """合并进 worker_config 字典；已有 provider_selection 时拒绝静默覆盖。"""
    config = dict(worker_config)
    if config.get("provider_selection") is not None:
        raise ValueError("provider_selection already recorded; mid-run swap forbidden")
    config["provider_selection"] = selection.model_dump(mode="json")
    return config


def read_provider_selection(worker_config: dict[str, Any]) -> ProviderSelection | None:
    raw = worker_config.get("provider_selection")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise TypeError("worker_config.provider_selection must be an object")
    return ProviderSelection.model_validate(raw)
