"""P7 M2：ProviderRouter 可重放选择与 worker_config 持久化。"""

from __future__ import annotations

import pytest

from stableagent.orchestration.model import Assignment
from stableagent.providers.routing import (
    ProviderCandidate,
    ProviderRouter,
    RoutingRequest,
    merge_provider_selection,
    read_provider_selection,
)


def _candidates() -> list[ProviderCandidate]:
    return [
        ProviderCandidate(
            provider="openai",
            model="gpt-4o",
            capabilities=frozenset({"tools", "vision"}),
            unit_cost_hint=3.0,
        ),
        ProviderCandidate(
            provider="anthropic",
            model="claude-3-5-sonnet-20241022",
            capabilities=frozenset({"tools"}),
            unit_cost_hint=2.0,
        ),
        ProviderCandidate(
            provider="ollama",
            model="llama3.2",
            capabilities=frozenset({"tools"}),
            unit_cost_hint=0.1,
            healthy=False,
        ),
    ]


def test_select_is_deterministic_and_prefers_cheap_healthy() -> None:
    router = ProviderRouter(_candidates())
    request = RoutingRequest(
        tenant_id="t1",
        task_class="code",
        required_capabilities=frozenset({"tools"}),
        prefer_cheap=True,
        price_table_version="price-v1",
    )
    first = router.select(request)
    second = router.select(request)
    assert first == second
    assert first.provider == "anthropic"
    assert first.model == "claude-3-5-sonnet-20241022"
    assert "prefer_cheap" in first.reason_codes
    assert first.fallback_chain[0]["provider"] == "openai"
    assert first.price_table_version == "price-v1"


def test_unhealthy_and_capability_filtered() -> None:
    router = ProviderRouter(_candidates())
    with pytest.raises(LookupError):
        router.select(RoutingRequest(required_capabilities=frozenset({"vision", "audio"})))
    selection = router.select(
        RoutingRequest(required_capabilities=frozenset({"vision"}), prefer_cheap=True)
    )
    assert selection.provider == "openai"


def test_stable_order_when_not_prefer_cheap() -> None:
    router = ProviderRouter(_candidates())
    selection = router.select(
        RoutingRequest(required_capabilities=frozenset({"tools"}), prefer_cheap=False)
    )
    assert selection.provider == "anthropic"
    assert "stable_order" in selection.reason_codes


def test_merge_and_read_selection_roundtrip() -> None:
    router = ProviderRouter(_candidates())
    selection = router.select(RoutingRequest(prefer_cheap=True))
    assignment = Assignment(id="as-1", ticket_id="tk-1")
    config = merge_provider_selection(assignment.worker_config, selection)
    updated = assignment.model_copy(update={"worker_config": config})
    loaded = read_provider_selection(updated.worker_config)
    assert loaded == selection
    with pytest.raises(ValueError, match="already recorded"):
        merge_provider_selection(updated.worker_config, selection)
    with pytest.raises(TypeError):
        read_provider_selection({"provider_selection": "bad"})
