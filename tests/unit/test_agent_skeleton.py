"""Agent 骨架：可导入、可构造、暂不执行（P0 DoD）。"""

import pytest

import stableagent
from stableagent import Agent
from stableagent.core.state import Config


def test_agent_is_constructible_without_config() -> None:
    agent = Agent(name="demo", provider="openai:gpt-4o")
    assert agent.name == "demo"
    assert isinstance(agent.config, Config)


@pytest.mark.parametrize("method", ["run", "stream"])
def test_execution_surface_exists_but_defers_to_p1(method: str) -> None:
    agent = Agent(name="demo")
    with pytest.raises(NotImplementedError):
        getattr(agent, method)("some task")


async def test_arun_defers_to_p1() -> None:
    agent = Agent(name="demo")
    with pytest.raises(NotImplementedError):
        await agent.arun("some task")


def test_package_metadata() -> None:
    assert stableagent.__version__ == "0.1.0"
    assert stableagent.CONTRACT_VERSION == "1.0.0"
