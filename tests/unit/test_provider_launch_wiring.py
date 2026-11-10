"""M2 最小接线：launch 前 select + 预算包装；硬超支不调用内层。"""

from __future__ import annotations

from typing import Any

import pytest

from stableagent.control.cost import (
    BudgetedProvider,
    BudgetPolicy,
    CostLedger,
    HardBudgetExceeded,
    ModelPrice,
    PricingTable,
)
from stableagent.core.state import Message
from stableagent.orchestration.model import Assignment
from stableagent.providers.base import Provider, ProviderChunk, ProviderResponse
from stableagent.providers.routing import (
    ProviderCandidate,
    ProviderRouter,
    RoutingRequest,
    merge_provider_selection,
    read_provider_selection,
)


class _Inner(Provider):
    def __init__(self) -> None:
        self.calls = 0

    def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        **options: Any,
    ) -> ProviderResponse:
        self.calls += 1
        return ProviderResponse(content="ok", usage={"prompt_tokens": 1})

    async def acomplete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        **options: Any,
    ) -> ProviderResponse:
        self.calls += 1
        return ProviderResponse(content="ok", usage={"prompt_tokens": 1})

    async def stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        **options: Any,
    ):
        yield ProviderChunk(done=True)


async def test_launch_selects_then_budget_blocks() -> None:
    router = ProviderRouter(
        [
            ProviderCandidate(
                provider="ollama",
                model="llama3.2",
                capabilities=frozenset({"tools"}),
                unit_cost_hint=0.1,
            )
        ]
    )
    selection = router.select(
        RoutingRequest(prefer_cheap=True, price_table_version="price-v1")
    )
    assignment = Assignment(id="as-1", ticket_id="tk-1")
    assignment = assignment.model_copy(
        update={
            "worker_config": merge_provider_selection(assignment.worker_config, selection)
        }
    )
    assert read_provider_selection(assignment.worker_config) is not None

    ledger = CostLedger(
        pricing=PricingTable(
            version="price-v1",
            prices=(
                ModelPrice(
                    provider="ollama", model="llama3.2", input_per_1k=0.0, output_per_1k=0.0
                ),
            ),
        ),
        policy=BudgetPolicy(hard_max_tokens=1),
    )
    ledger.record_usage(
        provider="ollama", model="llama3.2", usage={"prompt_tokens": 5, "completion_tokens": 0}
    )
    inner = _Inner()
    wrapped = BudgetedProvider(
        inner, ledger, provider_name=selection.provider, model=selection.model
    )
    with pytest.raises(HardBudgetExceeded):
        await wrapped.acomplete([Message(role="user", content="x")])
    assert inner.calls == 0
