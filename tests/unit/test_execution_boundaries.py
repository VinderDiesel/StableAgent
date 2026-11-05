"""execution 包口径：独立版本常量、依赖方向锁、稳定错误家族（ADR-05 决策 1/6）。

Task 1（步骤 2.1 前半）落位；路径收口的行为用例在 `tests/unit/test_worktree.py`（C4）。
"""

import ast
import pathlib

import stableagent
from stableagent.execution import EXECUTION_SCHEMA_VERSION
from stableagent.execution import errors as error_module
from stableagent.execution.errors import ExecutionError

ROOT = pathlib.Path(stableagent.__file__).parent


def _imports_of(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def _execution_modules() -> list[pathlib.Path]:
    return sorted((ROOT / "execution").rglob("*.py"))


def test_execution_version_is_independent_and_not_reexported() -> None:
    assert EXECUTION_SCHEMA_VERSION == "0.1.0"
    assert "ExecutionError" not in dir(stableagent)  # 顶层不重导出（同 control/orchestration 口径）


def test_execution_imports_only_the_layers_below_it() -> None:
    """L2 方向锁：execution 只可 import control/orchestration/core；integrations 在它上游。"""
    allowed = (
        "stableagent.execution",  # 包内自引用
        "stableagent.control",
        "stableagent.orchestration",
        "stableagent.core",
    )
    offenders: list[tuple[str, list[str]]] = []
    for module in _execution_modules():
        stray = [
            name
            for name in _imports_of(module)
            if name.startswith("stableagent.") and not name.startswith(allowed)
        ]
        if stray:
            offenders.append((str(module.relative_to(ROOT.parent)), sorted(stray)))
    assert not offenders, offenders


def test_execution_never_imports_integrations() -> None:
    offenders = [
        str(module.relative_to(ROOT.parent))
        for module in _execution_modules()
        if "stableagent.integrations" in _imports_of(module)
    ]
    assert not offenders


def _all_error_classes() -> list[type]:
    return [
        obj
        for _, obj in vars(error_module).items()
        if isinstance(obj, type) and issubclass(obj, ExecutionError)
    ]


def test_execution_error_code_inventory_is_unique_and_prefixed() -> None:
    assert issubclass(ExecutionError, ValueError)
    probes = {
        "PathEscapeError": (pathlib.Path("/root"), "/root/../etc/passwd"),
        "WorktreeReuseError": ("base_ref 不符", {"base_ref": "main"}),
        "CommandDeniedError": ("command_not_allowed", ("rm", "-rf")),
        "CleanupNotConfirmedError": (pathlib.Path("/root/wt"),),
    }
    codes = {}
    for name, args in probes.items():
        error = getattr(error_module, name)(*args)
        assert error.code.startswith("execution.")
        tail = error.code.split(".", 1)[1]
        assert tail.replace("_", "").isalnum() and tail == tail.lower()  # snake_case
        assert error.detail  # 每条都有可读详情
        codes[name] = error.code
    assert len(set(codes.values())) == len(codes)  # 唯一
    assert {c.__name__ for c in _all_error_classes()} == {"ExecutionError", *probes}
