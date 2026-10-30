"""Agent 装配（P1 后）：可构造；字符串 Provider 在运行时给出明确错误。"""

import pytest

import stableagent
from stableagent import Agent
from stableagent.core.state import CONTRACT_VERSION, Config


def test_agent_is_constructible_without_config() -> None:
    agent = Agent(name="demo", provider="openai:gpt-4o")
    assert agent.name == "demo"
    assert isinstance(agent.config, Config)


def test_string_provider_rejected_at_run() -> None:
    agent = Agent(name="demo")
    with pytest.raises(ValueError, match="Provider 实例"):
        agent.run("some task")


async def test_string_provider_rejected_at_arun() -> None:
    agent = Agent(name="demo")
    with pytest.raises(ValueError, match="Provider 实例"):
        await agent.arun("some task")


def test_package_metadata() -> None:
    assert stableagent.__version__ == "0.1.0"
    assert CONTRACT_VERSION == "1.1.0"
