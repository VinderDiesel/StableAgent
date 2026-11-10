"""P7 M6：离线演示 AutoRule 开 → inferred ACCEPT → 关 → 回 OBSERVE。

用法：
    python examples/07_auto_rule_offline.py
"""

from __future__ import annotations

from pathlib import Path

from stableagent.control.model import Action
from stableagent.control.policy import StaticPolicy, StaticPolicyConfig
from stableagent.supervisor.assessor import ObserveOnlyAssessor
from stableagent.supervisor.auto import AutoGate, AutoRule
from stableagent.supervisor.model import (
    AutonomyMode,
    DecisionOutcome,
    Thread,
    ThreadKind,
    ThreadStatus,
    Trigger,
    TriggerKind,
)
from stableagent.supervisor.router import RouterConfig, RouterService
from stableagent.supervisor.store import SupervisorStore


class DemoClock:
    def __init__(self, start: int = 1_000) -> None:
        self._now = start

    def now_ms(self) -> int:
        return self._now


class DemoIds:
    def __init__(self) -> None:
        self._n: dict[str, int] = {}

    def new_id(self, prefix: str) -> str:
        self._n[prefix] = self._n.get(prefix, 0) + 1
        return f"{prefix}-{self._n[prefix]:04d}"


def main() -> None:
    clock = DemoClock()
    ids = DemoIds()
    root = Path(".demo-auto")
    root.mkdir(exist_ok=True)
    store = SupervisorStore(root / "m6.db", clock=clock)
    policy = StaticPolicy(
        StaticPolicyConfig(
            repository="o/r",
            actors=frozenset({"bot"}),
            grantable_actions=frozenset({Action.READ_REPOSITORY}),
            default_validity_ms=60_000,
        ),
        clock=clock,
        ids=ids,
    )
    gate = AutoGate()
    gate.register(
        AutoRule(id="demo", tenant_id="default", repository="o/r", enabled=False),
        operator="admin",
    )
    router = RouterService(
        store,
        policy,
        ObserveOnlyAssessor(),
        RouterConfig(
            repository="o/r",
            mode=AutonomyMode.AUTO,
            actions=("read_repository",),
        ),
        clock=clock,
        ids=ids,
        auto_gate=gate,
    )
    with store.transaction():
        store.upsert_thread(
            Thread(
                key="issue:o/r#1",
                repository="o/r",
                kind=ThreadKind.ISSUE,
                external_ref="#1",
                last_trigger_at=1,
                status=ThreadStatus.OPEN,
            )
        )

    def fire(eid: str) -> DecisionOutcome:
        trig = Trigger(
            id=f"tg-{eid}",
            external_id=eid,
            kind=TriggerKind.ISSUE,
            actor="bot",
            thread_keys=["issue:o/r#1"],
            occurred_at=1,
            received_at=1,
            payload={"resource": {"number": 1}},
        )
        reception = router.receive(trig)
        decision = store.get_decision(reception.decision_ids[0])
        assert decision is not None
        return decision.outcome

    before = fire("github:o/r:issue:1")
    gate.enable("demo", operator="admin")
    during = fire("github:o/r:issue:2")
    gate.disable("demo", operator="admin")
    after = fire("github:o/r:issue:3")
    print(f"AUTO_DEMO before={before.value} during={during.value} after={after.value}")
    assert before is DecisionOutcome.OBSERVE
    assert during is DecisionOutcome.ACCEPT
    assert after is DecisionOutcome.OBSERVE
    print("AUTO_DEMO ok")


if __name__ == "__main__":
    main()
