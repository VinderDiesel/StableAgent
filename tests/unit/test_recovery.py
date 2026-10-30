"""C/E 节单测：错误分类、指数退避、恢复决策、快照版本迁移。"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from stableagent.core.checkpoint.recovery import (
    MIGRATIONS,
    RecoveryDirector,
    RetryPolicy,
    SnapshotVersionError,
    migrate_payload,
    migrate_state,
    pause_for_human,
    repair_for_retry,
)
from stableagent.core.errors import ErrorClass, classify_error, classify_error_detail
from stableagent.core.state import CONTRACT_VERSION, AgentState, AgentStatus, Message

FIXTURE = Path(__file__).parent.parent / "fixtures" / "state_v1_0_0.json"


class _StatusError(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(f"HTTP/1.1 {code} Internal Server Error")
        self.status_code = code


# ------------------------------------------------------------------ C3 错误分类


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (TimeoutError("read timed out"), ErrorClass.RETRYABLE),
        (ConnectionError("connection reset by peer"), ErrorClass.RETRYABLE),
        (_StatusError(429), ErrorClass.RETRYABLE),
        (_StatusError(503), ErrorClass.RETRYABLE),
        (_StatusError(400), ErrorClass.NON_RETRYABLE),
        (_StatusError(403), ErrorClass.NEEDS_HUMAN),
        (PermissionError("permission denied"), ErrorClass.NEEDS_HUMAN),
        (ValueError("bad schema"), ErrorClass.NON_RETRYABLE),
    ],
)
def test_c3_classify_error(exc: Exception, expected: ErrorClass) -> None:
    assert classify_error(exc) is expected


def test_c3_unknown_defaults_to_non_retryable() -> None:
    assert classify_error(RuntimeError("某种没见过的故障")) is ErrorClass.NON_RETRYABLE


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (httpx.ConnectTimeout("connect timed out"), ErrorClass.RETRYABLE),
        (httpx.ReadTimeout("read timeout"), ErrorClass.RETRYABLE),
        (httpx.WriteTimeout("write timeout"), ErrorClass.RETRYABLE),
        (httpx.RemoteProtocolError("bad gateway"), ErrorClass.RETRYABLE),
        (httpx.PoolTimeout("pool timeout"), ErrorClass.RETRYABLE),
    ],
)
def test_c5_provider_timeout_shapes_are_retryable(exc: Exception, expected: ErrorClass) -> None:
    """C5 后半：request_timeout_seconds 触发的 httpx 超时必须落进退避重试那一类。"""
    assert classify_error(exc) is expected


@pytest.mark.parametrize(
    ("detail", "expected"),
    [
        ("TimeoutError: gateway timed out", ErrorClass.RETRYABLE),
        ("openai.RateLimitError: Error code: 429", ErrorClass.RETRYABLE),
        ("APIStatusError: status_code: 451 unavailable", ErrorClass.NEEDS_HUMAN),
        ("InvalidRequestError: status_code: 400 bad request", ErrorClass.NON_RETRYABLE),
        ("KeyError: 'nothing'", ErrorClass.NON_RETRYABLE),
    ],
)
def test_c3_classify_p1_normalized_detail(detail: str, expected: ErrorClass) -> None:
    assert classify_error_detail(detail) is expected


# ---------------------------------------------------------------- C4 指数退避


def test_c4_backoff_sequence_is_geometric_and_capped() -> None:
    policy = RetryPolicy(max_attempts=4, base_delay_seconds=1.0, backoff_factor=2.0)
    assert policy.delays() == [1.0, 2.0, 4.0]
    uncapped = RetryPolicy(
        max_attempts=12, base_delay_seconds=1.0, backoff_factor=2.0, max_delay_seconds=1e9
    )
    assert uncapped.delay_for(10) == 2**9


def test_c4_max_delay_caps_the_sequence() -> None:
    policy = RetryPolicy(
        max_attempts=8, base_delay_seconds=2.0, backoff_factor=3.0, max_delay_seconds=15.0
    )
    assert policy.delays() == [2.0, 6.0, 15.0, 15.0, 15.0, 15.0, 15.0]
    with pytest.raises(ValueError, match="attempt 从 1 起算"):
        policy.delay_for(0)


# ---------------------------------------------------------------- C2/C6 恢复决策


def _director(strategy: str, *, attempts_config: int = 3, providers: int = 2) -> RecoveryDirector:
    return RecoveryDirector(
        strategy=strategy,
        policy=RetryPolicy(max_attempts=attempts_config, base_delay_seconds=0.5),
        provider_count=providers,
    )


def test_c2_none_strategy_never_retries() -> None:
    decision = _director("none").decide("TimeoutError: boom")
    assert decision.action == "fail"


def test_c2_retryable_gets_backoff_and_provider_rotation() -> None:
    director = _director("retry_with_fallback", attempts_config=4)
    first = director.decide("TimeoutError: boom", attempts=0)
    second = director.decide("TimeoutError: boom", attempts=1)
    third = director.decide("TimeoutError: boom", attempts=2)
    assert (first.action, first.delay_seconds, first.provider_index) == ("retry", 0.5, 1)
    assert (second.action, second.delay_seconds, second.provider_index) == ("retry", 1.0, 0)
    assert (third.action, third.delay_seconds, third.provider_index) == ("retry", 2.0, 1)
    assert (first.attempt, second.attempt, third.attempt) == (1, 2, 3)
    # max_attempts=4 含首次调用：第 4 次仍失败就认了
    assert director.decide("TimeoutError: boom", attempts=3).action == "fail"


def test_c2_retries_exhaust_then_fail() -> None:
    decision = _director("retry_with_fallback", attempts_config=2).decide(
        "TimeoutError: boom", attempts=2
    )
    assert decision.action == "fail"
    assert decision.error_class is ErrorClass.RETRYABLE


def test_c6_needs_human_pauses_only_when_recovery_enabled() -> None:
    assert _director("resume").decide("PermissionError: 需要授权").action == "needs_human"
    assert _director("none").decide("PermissionError: 需要授权").action == "fail"


def test_c6_non_retryable_fails_immediately() -> None:
    assert _director("retry_with_fallback").decide("ValueError: 参数错").action == "fail"


# ---------------------------------------------------------------- E 节版本迁移


def test_e2_v1_0_0_snapshot_upgrades_with_defaults() -> None:
    state = migrate_state(FIXTURE.read_text())
    assert state.contract_version == CONTRACT_VERSION
    assert state.usage == {}  # v1.1.0 新增字段的默认补齐
    assert all(message.tool_calls == [] for message in state.messages)
    assert state.status is AgentStatus.RUNNING
    assert len(state.messages) == 3  # 历史消息一字未动


def test_e1_missing_version_is_treated_as_earliest_known() -> None:
    data = json.loads(FIXTURE.read_text())
    data.pop("contract_version")
    assert migrate_payload(data)["contract_version"] == CONTRACT_VERSION


def test_e1_unknown_and_future_versions_fail_loudly() -> None:
    with pytest.raises(SnapshotVersionError, match="无迁移路径"):
        migrate_state({"contract_version": "0.9.0", "run_id": "r", "agent_name": "t"})
    with pytest.raises(SnapshotVersionError, match="高于当前契约"):
        migrate_state({"contract_version": "9.9.9", "run_id": "r", "agent_name": "t"})
    with pytest.raises(SnapshotVersionError, match="无法解析契约版本"):
        migrate_state({"contract_version": "v1-beta", "run_id": "r", "agent_name": "t"})


def test_e1_migration_registry_is_registered_chain() -> None:
    assert MIGRATIONS["1.0.0"][0] == "1.1.0"


def test_e3_snapshot_roundtrip_is_lossless() -> None:
    state = AgentState(
        run_id="r",
        agent_name="t",
        status=AgentStatus.RUNNING,
        step=2,
        messages=[
            Message(role="user", content="q"),
            Message(role="assistant", content="a", tool_calls=[]),
        ],
        usage={"prompt_tokens": 3},
    )
    assert AgentState.loads(state.snapshot()) == state
    assert migrate_state(state.snapshot()) == state


def test_state_repair_helpers() -> None:
    failed = AgentState(
        run_id="r",
        agent_name="t",
        status=AgentStatus.FAILED,
        step=2,
        error="TimeoutError: boom",
    )
    repaired = repair_for_retry(failed)
    assert repaired.status is AgentStatus.RUNNING and repaired.error is None
    assert repaired.step == failed.step

    paused = pause_for_human(failed)
    assert paused.status is AgentStatus.PAUSED
    assert paused.error == "TimeoutError: boom"  # 保留错误文本供人判读
