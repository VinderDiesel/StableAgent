"""成本与预算：价格表、软/硬阈值、账本；硬超支必须阻止 Provider 调用。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from stableagent.core.state import Message
from stableagent.providers.base import Provider, ProviderChunk, ProviderResponse


class BudgetOutcome(str, Enum):
    OK = "ok"
    SOFT_EXCEED = "soft_exceed"
    HARD_EXCEED = "hard_exceed"


class HardBudgetExceeded(Exception):
    """硬预算已达上限：调用方不得再打 Provider。"""

    def __init__(self, message: str, *, decision: BudgetDecision) -> None:
        super().__init__(message)
        self.decision = decision


class SoftBudgetExceeded(Exception):
    """软预算提示：外层可转审批/暂停；不强制阻断本里程碑之外的路径。"""

    def __init__(self, message: str, *, decision: BudgetDecision) -> None:
        super().__init__(message)
        self.decision = decision


class ModelPrice(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str
    model: str
    input_per_1k: float
    output_per_1k: float
    currency: str = "USD"


class PricingTable(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str
    currency: str = "USD"
    timezone: str = "UTC"
    prices: tuple[ModelPrice, ...] = ()

    def lookup(self, provider: str, model: str) -> ModelPrice | None:
        for price in self.prices:
            if price.provider == provider and price.model == model:
                return price
        return None

    def estimate_cost(
        self,
        *,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> tuple[float, bool]:
        """返回 (cost, estimated)。无价格表条目时 cost=0 且 estimated=True。"""
        price = self.lookup(provider, model)
        if price is None:
            return 0.0, True
        cost = (prompt_tokens / 1000.0) * price.input_per_1k + (
            completion_tokens / 1000.0
        ) * price.output_per_1k
        return cost, False


class BudgetPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    soft_max_tokens: int | None = None
    hard_max_tokens: int | None = None
    soft_max_cost: float | None = None
    hard_max_cost: float | None = None
    currency: str = "USD"


class BudgetDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: BudgetOutcome
    total_tokens: int
    total_cost: float
    currency: str
    price_table_version: str
    reason_codes: list[str] = Field(default_factory=list)
    estimated: bool = False


@dataclass
class CostLedger:
    pricing: PricingTable
    policy: BudgetPolicy
    total_tokens: int = 0
    total_cost: float = 0.0
    estimated_any: bool = False
    entries: list[dict[str, Any]] = field(default_factory=list)

    def record_usage(
        self,
        *,
        provider: str,
        model: str,
        usage: dict[str, int] | None,
        estimated_prompt: int = 0,
        estimated_completion: int = 0,
    ) -> BudgetDecision:
        if usage and (
            "prompt_tokens" in usage
            or "completion_tokens" in usage
            or "total_tokens" in usage
        ):
            prompt = int(usage.get("prompt_tokens") or 0)
            completion = int(usage.get("completion_tokens") or 0)
            if not prompt and not completion and "total_tokens" in usage:
                prompt = int(usage["total_tokens"])
            estimated = False
        else:
            prompt = estimated_prompt
            completion = estimated_completion
            estimated = True
        cost, price_missing = self.pricing.estimate_cost(
            provider=provider,
            model=model,
            prompt_tokens=prompt,
            completion_tokens=completion,
        )
        estimated = estimated or price_missing
        tokens = prompt + completion
        self.total_tokens += tokens
        self.total_cost += cost
        self.estimated_any = self.estimated_any or estimated
        self.entries.append(
            {
                "provider": provider,
                "model": model,
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "cost": cost,
                "estimated": estimated,
            }
        )
        return self.check()

    def check(self) -> BudgetDecision:
        reasons: list[str] = []
        outcome = BudgetOutcome.OK
        if (
            self.policy.hard_max_tokens is not None
            and self.total_tokens >= self.policy.hard_max_tokens
        ):
            outcome = BudgetOutcome.HARD_EXCEED
            reasons.append("hard_max_tokens")
        if (
            self.policy.hard_max_cost is not None
            and self.total_cost >= self.policy.hard_max_cost
        ):
            outcome = BudgetOutcome.HARD_EXCEED
            reasons.append("hard_max_cost")
        if outcome is BudgetOutcome.OK:
            if (
                self.policy.soft_max_tokens is not None
                and self.total_tokens >= self.policy.soft_max_tokens
            ):
                outcome = BudgetOutcome.SOFT_EXCEED
                reasons.append("soft_max_tokens")
            if (
                self.policy.soft_max_cost is not None
                and self.total_cost >= self.policy.soft_max_cost
            ):
                outcome = BudgetOutcome.SOFT_EXCEED
                reasons.append("soft_max_cost")
        return BudgetDecision(
            outcome=outcome,
            total_tokens=self.total_tokens,
            total_cost=round(self.total_cost, 8),
            currency=self.policy.currency,
            price_table_version=self.pricing.version,
            reason_codes=reasons,
            estimated=self.estimated_any,
        )

    def require_before_call(self, *, raise_soft: bool = False) -> BudgetDecision:
        """调用 Provider 前检查；硬超支抛 HardBudgetExceeded。"""
        decision = self.check()
        if decision.outcome is BudgetOutcome.HARD_EXCEED:
            raise HardBudgetExceeded(
                f"hard budget exceeded: {decision.reason_codes}",
                decision=decision,
            )
        if raise_soft and decision.outcome is BudgetOutcome.SOFT_EXCEED:
            raise SoftBudgetExceeded(
                f"soft budget exceeded: {decision.reason_codes}",
                decision=decision,
            )
        return decision


class BudgetedProvider(Provider):
    """外层包装：硬预算阻断在 Core 之外；不把租户规则塞进 AgentLoop。"""

    def __init__(
        self,
        inner: Provider,
        ledger: CostLedger,
        *,
        provider_name: str,
        model: str,
        raise_soft: bool = False,
    ) -> None:
        self._inner = inner
        self._ledger = ledger
        self._provider_name = provider_name
        self._model = model
        self._raise_soft = raise_soft

    def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        **options: Any,
    ) -> ProviderResponse:
        self._ledger.require_before_call(raise_soft=self._raise_soft)
        response = self._inner.complete(messages, tools=tools, **options)
        self._ledger.record_usage(
            provider=self._provider_name, model=self._model, usage=response.usage
        )
        return response

    async def acomplete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        **options: Any,
    ) -> ProviderResponse:
        self._ledger.require_before_call(raise_soft=self._raise_soft)
        response = await self._inner.acomplete(messages, tools=tools, **options)
        self._ledger.record_usage(
            provider=self._provider_name, model=self._model, usage=response.usage
        )
        return response

    async def stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        **options: Any,
    ) -> AsyncIterator[ProviderChunk]:
        self._ledger.require_before_call(raise_soft=self._raise_soft)
        async for chunk in self._inner.stream(messages, tools=tools, **options):
            yield chunk
