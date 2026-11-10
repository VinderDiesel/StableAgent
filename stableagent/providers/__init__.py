"""Provider 适配层：base / openai / anthropic / ollama / routing（P7 M2）。"""

from __future__ import annotations

from stableagent.providers.anthropic import AnthropicProvider
from stableagent.providers.base import Provider, ProviderChunk, ProviderResponse
from stableagent.providers.ollama import OllamaProvider
from stableagent.providers.openai import OpenAIProvider
from stableagent.providers.routing import (
    ProviderCandidate,
    ProviderRouter,
    ProviderSelection,
    RoutingRequest,
    merge_provider_selection,
    read_provider_selection,
)

__all__ = [
    "AnthropicProvider",
    "OllamaProvider",
    "OpenAIProvider",
    "Provider",
    "ProviderCandidate",
    "ProviderChunk",
    "ProviderResponse",
    "ProviderRouter",
    "ProviderSelection",
    "RoutingRequest",
    "merge_provider_selection",
    "read_provider_selection",
]
