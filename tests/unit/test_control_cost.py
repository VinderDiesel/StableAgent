"""P7 M2：成本账本与硬预算阻断。"""

from __future__ import annotations

from typing import Any

import pytest

from stableagent.control.cost import (
    BudgetedProvider,
    BudgetOutcome,
    BudgetPolicy,
    CostLedger,
    HardBudgetExceeded,
    ModelPrice,
    PricingTable,
    SoftBudgetExceeded,
)
from stableagent.core.state import Message
from stableagent.providers.base import Provider, ProviderChunk, ProviderResponse


class _CountingProvider(Provider):
    def __init__(self) -> None:
        self.calls = 0

    def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        **options: Any,
    ) -> ProviderResponse:
        self.calls += 1
        return ProviderResponse(
            content="ok", usage={"prompt_tokens": 100, "completion_tokens": 50}
        )

    async def acomplete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        **options: Any,
    ) -> ProviderResponse:
        self.calls += 1
        return ProviderResponse(
            content="ok", usage={"prompt_tokens": 100, "completion_tokens": 50}
        )

    async def stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        **options: Any,
    ):
        self.calls += 1
        yield ProviderChunk(text="x", done=True)


def _ledger(*, hard_tokens: int | None = None, soft_tokens: int | None = None) -> CostLedger:
    pricing = PricingTable(
        version="price-v1",
        prices=(
            ModelPrice(
                provider="openai",
                model="gpt-4o",
                input_per_1k=1.0,
                output_per_1k=2.0,
            ),
        ),
    )
    return CostLedger(
        pricing=pricing,
        policy=BudgetPolicy(
            soft_max_tokens=soft_tokens,
            hard_max_tokens=hard_tokens,
            soft_max_cost=None,
            hard_max_cost=None,
        ),
    )


def test_record_usage_prefers_actual_and_marks_estimate() -> None:
    ledger = _ledger()
    decision = ledger.record_usage(
        provider="openai",
        model="gpt-4o",
        usage={"prompt_tokens": 1000, "completion_tokens": 500},
    )
    assert decision.outcome is BudgetOutcome.OK
    assert decision.total_tokens == 1500
    assert decision.total_cost == 2.0  # 1*1 + 0.5*2
    assert decision.estimated is False
    assert decision.price_table_version == "price-v1"

    missing = ledger.record_usage(
        provider="unknown",
        model="x",
        usage=None,
        estimated_prompt=10,
        estimated_completion=5,
    )
    assert missing.estimated is True


def test_soft_and_hard_thresholds() -> None:
    ledger = _ledger(soft_tokens=100, hard_tokens=200)
    ledger.record_usage(
        provider="openai",
        model="gpt-4o",
        usage={"prompt_tokens": 100, "completion_tokens": 0},
    )
    soft = ledger.check()
    assert soft.outcome is BudgetOutcome.SOFT_EXCEED
    ledger.record_usage(
        provider="openai",
        model="gpt-4o",
        usage={"prompt_tokens": 100, "completion_tokens": 0},
    )
    hard = ledger.check()
    assert hard.outcome is BudgetOutcome.HARD_EXCEED
    assert "hard_max_tokens" in hard.reason_codes


async def test_budgeted_provider_blocks_hard_with_zero_inner_calls() -> None:
    inner = _CountingProvider()
    ledger = _ledger(hard_tokens=1)
    ledger.record_usage(
        provider="openai",
        model="gpt-4o",
        usage={"prompt_tokens": 10, "completion_tokens": 0},
    )
    wrapped = BudgetedProvider(inner, ledger, provider_name="openai", model="gpt-4o")
    with pytest.raises(HardBudgetExceeded):
        await wrapped.acomplete([Message(role="user", content="x")])
    assert inner.calls == 0


async def test_budgeted_provider_raise_soft() -> None:
    inner = _CountingProvider()
    ledger = _ledger(soft_tokens=1)
    ledger.record_usage(
        provider="openai",
        model="gpt-4o",
        usage={"prompt_tokens": 5, "completion_tokens": 0},
    )
    wrapped = BudgetedProvider(
        inner, ledger, provider_name="openai", model="gpt-4o", raise_soft=True
    )
    with pytest.raises(SoftBudgetExceeded):
        await wrapped.acomplete([Message(role="user", content="x")])
    assert inner.calls == 0


def test_hard_max_cost_blocks() -> None:
    pricing = PricingTable(
        version="price-v1",
        prices=(
            ModelPrice(
                provider="openai", model="gpt-4o", input_per_1k=10.0, output_per_1k=10.0
            ),
        ),
    )
    ledger = CostLedger(
        pricing=pricing, policy=BudgetPolicy(hard_max_cost=0.5, currency="USD")
    )
    ledger.record_usage(
        provider="openai",
        model="gpt-4o",
        usage={"prompt_tokens": 100, "completion_tokens": 0},
    )
    assert ledger.check().outcome is BudgetOutcome.HARD_EXCEED
    with pytest.raises(HardBudgetExceeded):
        ledger.require_before_call()


def test_budgeted_provider_complete_and_stream_record() -> None:
    inner = _CountingProvider()
    ledger = _ledger()
    wrapped = BudgetedProvider(inner, ledger, provider_name="openai", model="gpt-4o")
    assert wrapped.complete([Message(role="user", content="x")]).content == "ok"
    assert inner.calls == 1
    assert ledger.total_tokens == 150


async def test_budgeted_provider_stream_checks_budget() -> None:
    inner = _CountingProvider()
    ledger = _ledger(hard_tokens=1)
    ledger.record_usage(
        provider="openai", model="gpt-4o", usage={"prompt_tokens": 10, "completion_tokens": 0}
    )
    wrapped = BudgetedProvider(inner, ledger, provider_name="openai", model="gpt-4o")
    with pytest.raises(HardBudgetExceeded):
        async for _ in wrapped.stream([Message(role="user", content="x")]):
            pass
    assert inner.calls == 0
