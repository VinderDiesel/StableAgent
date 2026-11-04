"""控制层错误：每个实例带一个稳定 code，执行层/集成层与回写按 code 映射，不靠异常文案。

授权判定本身是数据（Decision / Admission），不抛错；本家族只用于"调用方违反了授权
前提"的场合——未知 mandate、重复决定、参数漂移、过期与越权请求。
"""

from __future__ import annotations


class ControlError(ValueError):
    """所有控制层用户可读错误的基类。

    沿用 P4 的口径：L2 禁止为错误类型再动 Core，所以本类直接继承 `ValueError`，
    用 `.code` 提供机器可判的稳定标识。
    """

    code = "control.error"

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.detail = message
        self.code = code


class AdmissionRejectedError(ControlError):
    """请求未通过受理门：不进授权链路，也不产生 Mandate。"""

    def __init__(self, reason: str) -> None:
        super().__init__("control.admission_rejected", f"请求未获受理：{reason}")


class UnknownMandateError(ControlError):
    def __init__(self, mandate_id: str) -> None:
        super().__init__("control.unknown_mandate", f"没有 mandate_id={mandate_id} 的记录")


class UnknownApprovalError(ControlError):
    def __init__(self, approval_id: str) -> None:
        super().__init__("control.unknown_approval", f"没有 approval_id={approval_id} 的记录")


class ApprovalDecidedError(ControlError):
    """审批已在终态，重复决定不改变任何事实。"""

    def __init__(self, approval_id: str, status: str) -> None:
        super().__init__(
            "control.approval_decided", f"审批 {approval_id} 已处于 {status}，不可再次决定"
        )


class StaleApprovalError(ControlError):
    """批准绑定的 `(action, resource_digest)` 与当前请求不一致：先批准再改参数不成立。"""

    def __init__(self, approval_id: str) -> None:
        super().__init__(
            "control.stale_approval",
            f"审批 {approval_id} 绑定的参数已变化，授权失效",
        )


class MandateExpiredError(ControlError):
    def __init__(self, mandate_id: str, expires_at: int, now: int) -> None:
        super().__init__(
            "control.mandate_expired",
            f"mandate_id={mandate_id} 已于 {expires_at} 过期（now={now}）",
        )


class NotGrantedError(ControlError):
    """action 不在 Mandate 的授权集合内——包括 `enable_auto_merge` 这类点名拒绝的动作。"""

    def __init__(self, action: object, mandate_id: str) -> None:
        name = getattr(action, "value", action)
        super().__init__(
            "control.not_granted", f"动作 {name} 不在 mandate_id={mandate_id} 的授权集合内"
        )


class RevalidationFailedError(ControlError):
    """先判定后执行：某个 revalidate 时点未通过，或时点名不在受支持的集合内。"""

    def __init__(self, when: str, mandate_id: str) -> None:
        super().__init__(
            "control.revalidation_failed",
            f"{when} 时点的 revalidate 未通过：mandate_id={mandate_id}",
        )
