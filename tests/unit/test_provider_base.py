"""契约四：Provider 三种调用形态（sync / async / stream）。"""

from collections.abc import AsyncIterator
from typing import Any

import pytest

from stableagent.core.state import Message
from stableagent.providers.base import Provider, ProviderChunk, ProviderResponse


class DummyProvider(Provider):
    def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        **options: Any,
    ) -> ProviderResponse:
        return ProviderResponse(content="hi", usage={"prompt_tokens": len(messages)})

    async def acomplete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        **options: Any,
    ) -> ProviderResponse:
        return self.complete(messages, tools, **options)

    async def stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        **options: Any,
    ) -> AsyncIterator[ProviderChunk]:
        yield ProviderChunk(text="a")
        yield ProviderChunk(text="b", done=True)


def test_provider_is_abstract() -> None:
    with pytest.raises(TypeError):
        Provider()  # type: ignore[abstract]


def test_three_call_shapes_are_available() -> None:
    provider = DummyProvider()
    messages = [Message(role="user", content="x")]

    assert provider.complete(messages).content == "hi"
    assert provider.complete(messages).usage["prompt_tokens"] == 1


async def test_stream_terminates_with_done_chunk() -> None:
    provider = DummyProvider()
    chunks = [c async for c in provider.stream([Message(role="user", content="x")])]
    assert [c.text for c in chunks] == ["a", "b"]
    assert chunks[-1].done is True
