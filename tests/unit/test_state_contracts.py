"""契约一/二/三/六：Event / Message / AgentState / Config 的不变式测试。"""

import pytest
from pydantic import ValidationError

from stableagent.core.state import (
    CONTRACT_VERSION,
    AgentState,
    AgentStatus,
    Config,
    Event,
    EventType,
    Message,
)


def test_event_is_frozen() -> None:
    event = Event(type=EventType.THINKING, data={"text": "hi"}, step=1)
    with pytest.raises(ValidationError):
        event.step = 2  # type: ignore[misc]


def test_agent_state_snapshot_roundtrip_is_lossless() -> None:
    state = AgentState(
        run_id="r-1",
        agent_name="demo",
        status=AgentStatus.RUNNING,
        step=3,
        messages=[Message(role="user", content="hello")],
    )
    restored = AgentState.loads(state.snapshot())
    assert restored == state
    assert restored.contract_version == CONTRACT_VERSION


def test_paused_state_survives_serialization_for_p2_recovery() -> None:
    state = AgentState(run_id="r-2", agent_name="demo", status=AgentStatus.PAUSED, step=5)
    restored = AgentState.loads(state.snapshot())
    assert restored.status is AgentStatus.PAUSED
    assert restored.step == 5


def test_config_is_composable_with_defaults() -> None:
    config = Config()
    assert config.provider.model == "gpt-4o"
    assert config.checkpoint.backend == "memory"
    assert config.context.assembly == "passthrough"
    assert config.sandbox.backend == "none"
