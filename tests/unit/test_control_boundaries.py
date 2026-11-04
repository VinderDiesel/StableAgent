"""control 包口径：独立版本常量、顶层不重导出、稳定错误家族（ADR-05 决策 1）。

Task 8（步骤 1.8）追加 L2 依赖方向矩阵与 A2/L4 的扫描半边：三新包
（`control`/`execution`/`integrations`）× 全部旧包互不回头，且 `p5-v1` 的版本钉法
只允许出现在 `stableagent/control/`。
"""

import ast
import pathlib

import stableagent
from stableagent.control import CONTROL_SCHEMA_VERSION
from stableagent.control import errors as error_module
from stableagent.control.errors import ControlError
from stableagent.control.policy import P5_POLICY_VERSION

P5_VERSION_CONSTANT = "P5_POLICY_VERSION"  # 口径常量的名字：步骤 3 要求它全库只有一处定义

ROOT = pathlib.Path(stableagent.__file__).parent

# 三新包：`execution`/`integrations` 要到 M2/M4 才落包，名单先含三者（空目录 → 语料空转）。
P5_PACKAGES = ("control", "execution", "integrations")
# 计划的五旧包，作为"至少扫到这些"的下界（实际语料取 ROOT 下全部非 P5 包）。
LEGACY_PACKAGES = ("core", "context", "providers", "tools", "orchestration")
P5_IMPORT_PREFIXES = tuple(f"stableagent.{name}" for name in P5_PACKAGES)
# control 的上游：方向锁 integrations → execution → control → orchestration → core，反向不许。
UPSTREAM_OF_CONTROL = ("stableagent.execution", "stableagent.integrations")


def test_control_version_is_independent_and_not_reexported() -> None:
    assert CONTROL_SCHEMA_VERSION == "0.1.0"
    assert "ControlError" not in dir(stableagent)  # 顶层不重导出（同 P4 编排层口径）


def test_control_error_bases_valueerror() -> None:
    assert issubclass(ControlError, ValueError)
    error = ControlError("control.error", "boom")
    assert error.detail == "boom"
    assert str(error) == "[control.error] boom"


def _all_error_classes() -> list[type]:
    return [
        obj
        for _, obj in vars(error_module).items()
        if isinstance(obj, type) and issubclass(obj, ControlError)
    ]


def test_control_error_code_inventory_is_unique_and_prefixed() -> None:
    probes = {
        "AdmissionRejectedError": ("x",),
        "UnknownMandateError": ("x",),
        "UnknownApprovalError": ("x",),
        "ApprovalDecidedError": ("x", "approved"),
        "StaleApprovalError": ("x",),
        "MandateExpiredError": ("x", 1, 2),
        "NotGrantedError": ("x", "m"),
        "RevalidationFailedError": ("launch", "m"),
    }
    codes = {}
    for name, args in probes.items():
        error = getattr(error_module, name)(*args)
        assert error.code.startswith("control.")
        tail = error.code.split(".", 1)[1]
        assert tail.replace("_", "").isalnum() and tail == tail.lower()  # snake_case
        codes[name] = error.code
    assert len(set(codes.values())) == len(codes)  # 唯一
    assert {c.__name__ for c in _all_error_classes()} == {"ControlError", *probes}


# ---------------------------------------------------------------- 扫描器（Task 8 / 步骤 1.8）


def _legacy_packages() -> list[str]:
    """ROOT 下除三新包以外的全部包目录——旧包新增目录不需要改名单，反向门自动覆盖。"""
    return sorted(
        path.name
        for path in ROOT.iterdir()
        if path.is_dir() and path.name not in {*P5_PACKAGES, "__pycache__"}
    )


def _modules(*packages: str) -> list[pathlib.Path]:
    return sorted(p for name in packages for p in (ROOT / name).rglob("*.py"))


def _label(path: pathlib.Path) -> str:
    return str(path.relative_to(ROOT.parent))


def _imports_of(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def _mandate_calls(path: pathlib.Path) -> list[ast.Call]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name == "Mandate":
            calls.append(node)
    return calls


def _pins_p5_version(call: ast.Call) -> bool:
    """`policy_version=` 被静态钉成 p5-v1：字面量，或直接引用 control 的口径常量。"""
    for keyword in call.keywords:
        if keyword.arg != "policy_version":
            continue
        value = keyword.value
        if isinstance(value, ast.Constant) and value.value == P5_POLICY_VERSION:
            return True
        if isinstance(value, ast.Name) and value.id == P5_VERSION_CONSTANT:
            return True
    return False


def _imports_version_constant(path: pathlib.Path) -> bool:
    """`from stableagent.control.policy import P5_POLICY_VERSION` 形态的引用。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == "stableagent.control.policy"
            and any(alias.name == P5_VERSION_CONSTANT for alias in node.names)
        ):
            return True
    return False


def _version_constant_names(path: pathlib.Path) -> list[str]:
    """把 `p5-v1` 字面量绑到模块级常量名上的赋值，返回这些常量名。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Constant)
            and node.value.value == P5_POLICY_VERSION
        ):
            names += [target.id for target in node.targets if isinstance(target, ast.Name)]
    return names


def test_no_legacy_package_imports_p5_layers() -> None:
    """L2 反向：旧包永远不 import 三新包——授权事实往下游走，不回头。"""
    packages = _legacy_packages()
    assert set(packages) >= set(LEGACY_PACKAGES), packages
    offenders: list[tuple[str, list[str]]] = []
    for module in _modules(*packages):
        banned = sorted(name for name in _imports_of(module) if name.startswith(P5_IMPORT_PREFIXES))
        if banned:
            offenders.append((_label(module), banned))
    assert not offenders


def test_control_never_imports_execution_or_integrations() -> None:
    """L2 正向：control 只依赖 orchestration/core，执行层与集成层在它的上游。"""
    offenders: list[tuple[str, list[str]]] = []
    for module in _modules("control"):
        banned = sorted(
            name for name in _imports_of(module) if name.startswith(UPSTREAM_OF_CONTROL)
        )
        if banned:
            offenders.append((_label(module), banned))
    assert not offenders


def test_p5_v1_mandate_construction_is_confined_to_control() -> None:
    """A2 + L4 扫描半边：`p5-v1` 只有 `control.policy` 一处口径来源、一处钉版构造点。

    扫的是生产包 `stableagent/`；测试与示例不算越界（白盒用例直接构造 p5 Mandate 验证往返，
    见 `test_mandate_extension.py`），账本 L4 的口径是"生产代码里外部文本产不出授权"。
    """
    corpus = _modules(*(_legacy_packages() + list(P5_PACKAGES)))
    control = "stableagent/control"

    # ① 版本字面量只允许收敛为一个模块常量（步骤 3）。
    holders = sorted(_label(m) for m in corpus if _version_constant_names(m))
    assert holders == [f"{control}/policy.py"], holders
    assert _version_constant_names(ROOT / "control" / "policy.py") == [P5_VERSION_CONSTANT]

    # ② 该常量的 import 方只允许 control 包：别的包要用版本，读 Mandate 自己的字段。
    importers = sorted(_label(m) for m in corpus if _imports_version_constant(m))
    assert [item for item in importers if not item.startswith(control)] == [], importers

    # ③ control 包外把 policy_version 静态钉成 p5-v1 的 Mandate 构造点。
    pinned_outside = [
        (_label(module), call.lineno)
        for module in corpus
        if not _label(module).startswith(control)
        for call in _mandate_calls(module)
        if _pins_p5_version(call)
    ]
    assert pinned_outside == [], pinned_outside

    # 扫描器不是空转：A2 的唯一构造点必须被看见（`p5-v1` 字面量与它无关，走属性传递）。
    construction_sites = sorted(_label(m) for m in corpus if _mandate_calls(m))
    assert f"{control}/policy.py" in construction_sites, construction_sites
