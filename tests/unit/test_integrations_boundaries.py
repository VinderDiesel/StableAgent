"""integrations 包口径：独立版本、顶层不重导出、稳定错误家族、方向锁最上游。

M3 Task 1（步骤 3.1 前半）：新包落位即钉死 ADR-05 决策 1 的方向
integrations → execution → control → orchestration → core，反向不许。
"""

import ast
import pathlib

import stableagent
from stableagent.integrations import INTEGRATIONS_SCHEMA_VERSION
from stableagent.integrations import errors as error_module
from stableagent.integrations.errors import IntegrationsError

ROOT = pathlib.Path(stableagent.__file__).parent
ALLOWED = (
    "stableagent.integrations",
    "stableagent.execution",
    "stableagent.control",
    "stableagent.orchestration",
    "stableagent.core",
    "stableagent.tools",
)


def test_integrations_version_is_independent_and_not_reexported() -> None:
    assert INTEGRATIONS_SCHEMA_VERSION == "0.1.0"
    assert "IntegrationsError" not in dir(stableagent)


def test_integrations_error_bases_valueerror() -> None:
    assert issubclass(IntegrationsError, ValueError)
    error = IntegrationsError("integrations.error", "boom")
    assert error.detail == "boom"
    assert str(error) == "[integrations.error] boom"


def test_integrations_error_code_inventory_is_unique_and_prefixed() -> None:
    probes = {
        "McpProtocolError": ("bad envelope",),
        "McpTimeoutError": ("tools/list", 1.0),
        "McpProcessExitedError": (1,),
        "McpSchemaError": ("tools/list", "missing name"),
        "GitHubApiError": (404, "not found", "non_retryable"),
    }
    codes = {}
    for name, args in probes.items():
        error = getattr(error_module, name)(*args)
        assert error.code.startswith("integrations.")
        tail = error.code.split(".", 1)[1]
        assert tail == tail.lower() and tail.replace("_", "").isalnum()
        codes[name] = error.code
    assert len(set(codes.values())) == len(codes)
    assert {
        obj.__name__
        for obj in vars(error_module).values()
        if isinstance(obj, type) and issubclass(obj, IntegrationsError)
    } == {"IntegrationsError", *probes}


def _imports_of(path: pathlib.Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.append(node.module)
    return names


def test_integrations_imports_only_the_layers_below_it() -> None:
    offenders = {}
    for module in sorted((ROOT / "integrations").rglob("*.py")):
        stray = [
            name
            for name in _imports_of(module)
            if name.startswith("stableagent.") and not name.startswith(ALLOWED)
        ]
        if stray:
            offenders[str(module.relative_to(ROOT))] = stray
    assert not offenders, offenders


def test_mcp_module_never_imports_control_or_execution() -> None:
    """ADR-05 决策 8：mcp.py 只产出 Tool，授权守卫在 control 侧包外层。"""
    mcp = ROOT / "integrations" / "mcp.py"
    if not mcp.exists():  # Task 2 才落文件；规则先钉，落文件后自动生效
        return
    forbidden = ("stableagent.control", "stableagent.execution")
    assert not [name for name in _imports_of(mcp) if name.startswith(forbidden)]
