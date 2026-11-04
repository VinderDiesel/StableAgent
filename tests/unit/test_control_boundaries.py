"""control 包口径：独立版本常量、顶层不重导出、稳定错误家族（ADR-05 决策 1）。"""

import stableagent
from stableagent.control import CONTROL_SCHEMA_VERSION
from stableagent.control import errors as error_module
from stableagent.control.errors import ControlError


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
