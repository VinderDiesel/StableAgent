"""WP-6.3：observe 唯一评估器的确定性规则。"""

from __future__ import annotations

from stableagent.supervisor.assessor import (
    AssessmentContext,
    CandidateAssessor,
    ObserveOnlyAssessor,
)
from stableagent.supervisor.model import (
    AutonomyMode,
    Thread,
    ThreadKind,
    ThreadStatus,
    Trigger,
    TriggerKind,
)


def _trigger(kind: TriggerKind = TriggerKind.ISSUE) -> Trigger:
    return Trigger(
        id="tg-1",
        external_id="github:o/r:issue:1",
        kind=kind,
        actor="alice",
        thread_keys=["issue:o/r#1"],
        occurred_at=1,
        received_at=1,
        payload={"resource": {"number": 1}, "deleted": False},
    )


def _thread(status: ThreadStatus = ThreadStatus.OPEN) -> Thread:
    return Thread(
        key="issue:o/r#1",
        repository="o/r",
        kind=ThreadKind.ISSUE,
        external_ref="#1",
        last_trigger_at=1,
        status=status,
    )


def _context(**overrides: object) -> AssessmentContext:
    values: dict[str, object] = {
        "trigger": _trigger(),
        "thread": _thread(),
        "policy_version": "p5-v1",
    }
    values.update(overrides)
    return AssessmentContext(**values)


def test_autonomy_mode_has_three_values() -> None:
    assert [mode.value for mode in AutonomyMode] == ["off", "observe", "auto"]


def test_observe_only_assessor_marks_a_fresh_human_candidate() -> None:
    assessment = ObserveOnlyAssessor().assess(_context())
    assert assessment.would_accept is True
    assert assessment.reason_codes == ["inferred_candidate"]


def test_observe_only_assessor_rejects_ci_as_context_only() -> None:
    assessment = ObserveOnlyAssessor().assess(_context(trigger=_trigger(TriggerKind.CI_STATUS)))
    assert assessment.would_accept is False
    assert assessment.reason_codes == ["ci_context_only"]


def test_observe_only_assessor_rejects_non_open_threads() -> None:
    assessment = ObserveOnlyAssessor().assess(_context(thread=_thread(ThreadStatus.CLOSED)))
    assert assessment.would_accept is False
    assert assessment.reason_codes == ["thread_not_open"]


def test_observe_only_assessor_rejects_existing_tickets_and_subscriptions() -> None:
    existing = ObserveOnlyAssessor().assess(_context(existing_ticket_id="tk-1"))
    owned = ObserveOnlyAssessor().assess(_context(has_live_own_subscription=True))
    context = ObserveOnlyAssessor().assess(_context(has_live_context_subscription=True))
    assert existing.reason_codes == ["existing_ticket_context"]
    assert owned.reason_codes == ["already_owned"]
    assert context.reason_codes == ["context_only"]
    assert not existing.would_accept and not owned.would_accept and not context.would_accept


def test_structural_fake_satisfies_the_candidate_assessor_protocol() -> None:
    class Stub:
        def assess(self, context: AssessmentContext):
            return ObserveOnlyAssessor().assess(context)

    assert isinstance(Stub(), CandidateAssessor)
