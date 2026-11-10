"""L4 依赖方向 + L1 loop 零 diff + 编排层版本口径（ADR-04 决策 1）。"""

import ast
import hashlib
import pathlib

import pytest

import stableagent
from stableagent.orchestration import ORCHESTRATION_SCHEMA_VERSION
from stableagent.orchestration import errors as error_module
from stableagent.orchestration.errors import IdempotencyConflictError, OrchestrationError

ROOT = pathlib.Path(stableagent.__file__).parent

# P7 M2 批准碰 Core：内层硬超时后的 loop.py 内容哈希（ADR-07 决策 10）。
_LOOP_SHA256_AT_P7_M2 = "7f36273404fead0a9031c53dc127937c778656fd07df14f3b329a440e1306e6c"


def _imports_of(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


@pytest.mark.parametrize("package", ["core", "context", "providers", "tools"])
def test_core_layers_never_import_orchestration(package: str) -> None:
    for module in (ROOT / package).rglob("*.py"):
        assert "stableagent.orchestration" not in _imports_of(module), module


def test_orchestration_version_is_independent_and_not_reexported() -> None:
    assert ORCHESTRATION_SCHEMA_VERSION == "0.1.0"
    assert "Ticket" not in dir(stableagent)  # 顶层不重导出（P6 Beta 前不承诺稳定）


@pytest.mark.parametrize("module", ["store", "service", "outbox"])
def test_state_side_modules_never_import_scheduler_or_executor(module: str) -> None:
    """B7 + L6 的静态半边：Store/Service/Outbox 不 import 调度与执行侧，方向锁死。"""
    imports = _imports_of(ROOT / "orchestration" / f"{module}.py")
    banned = {"stableagent.orchestration.scheduler", "stableagent.orchestration.executor"}
    assert not imports & banned, (module, imports & banned)


def test_every_error_carries_a_stable_code() -> None:
    assert issubclass(OrchestrationError, ValueError)
    assert IdempotencyConflictError("x", "y").code == "orchestration.idempotency_conflict"


def test_loop_py_matches_p7_m2_hard_timeout_baseline() -> None:
    """L1：P7 M2 起允许硬超时改动；哈希锁住已批准基线，防无 ADR 漂移。"""
    digest = hashlib.sha256((ROOT / "core" / "loop.py").read_bytes()).hexdigest()
    assert digest == _LOOP_SHA256_AT_P7_M2


def test_agent_state_dump_has_no_ticket_fields() -> None:
    """L5：Core 的 `AgentState` 不被编排层污染——dump 里没有任何 Ticket/Mandate 字段。

    Ticket 与 Worker run_id 的关系只存在于 `Assignment.worker_run_id`（编排侧）。
    """
    from stableagent.core.state import AgentState

    dumped = AgentState(run_id="r", agent_name="a", messages=[]).model_dump()
    assert not [key for key in dumped if "ticket" in key.lower() or "mandate" in key.lower()]


def test_scheduler_and_service_never_import_planning() -> None:
    for module in ("service", "scheduler"):
        imports = _imports_of(ROOT / "orchestration" / f"{module}.py")
        assert not any("planning" in item for item in imports)  # E3：无 LLM Planner


def _all_error_classes() -> list[type]:
    return [
        obj
        for _, obj in vars(error_module).items()
        if isinstance(obj, type) and issubclass(obj, OrchestrationError)
    ]


def test_error_code_inventory_is_unique_and_prefixed() -> None:
    probes = {
        "IllegalTransitionError": ("m", "c", "a"),
        "UnknownTicketError": ("x",),
        "UnknownAssignmentError": ("x",),
        "ReentrantTransactionError": (),
        "DuplicateLaunchError": ("x",),
        "WorkerConfigError": ("x",),
        "IdempotencyConflictError": ("x", "y"),
        "CyclicDependencyError": ("x", ["a", "b"]),
        "DeferrableEffectError": ("x",),
        "PermanentEffectError": ("x",),
    }
    codes = {}
    for name, args in probes.items():
        error = getattr(error_module, name)(*args)
        assert error.code.startswith("orchestration.")
        tail = error.code.split(".", 1)[1]
        assert tail.replace("_", "").isalnum() and tail == tail.lower()  # snake_case
        codes[name] = error.code
    assert len(set(codes.values())) == len(codes)  # 唯一
    assert {c.__name__ for c in _all_error_classes()} == {"OrchestrationError", *probes}
