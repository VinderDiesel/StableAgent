"""L4 依赖方向 + 编排层版本口径（ADR-04 决策 1）。"""

import ast
import pathlib

import pytest

import stableagent
from stableagent.orchestration import ORCHESTRATION_SCHEMA_VERSION
from stableagent.orchestration.errors import IdempotencyConflictError, OrchestrationError

ROOT = pathlib.Path(stableagent.__file__).parent


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


def test_every_error_carries_a_stable_code() -> None:
    assert issubclass(OrchestrationError, ValueError)
    assert IdempotencyConflictError("x", "y").code == "orchestration.idempotency_conflict"
