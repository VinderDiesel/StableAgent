"""错误分类（P2 失败自愈的判据）：可重试 / 不可重试 / 需人工。

分类规则与理由见 docs/design-decisions/02-checkpoint.md。要点：
- 未知错误默认判为**不可重试**（宁可快速失败，不做无界重试）；
- 需人工（鉴权/权限/配额类）不靠重试解决，运行停在 PAUSED 等人处理；
- 兼容 P1 的归一化字符串（`loop` 写入 `state.error` 的 `"{类型名}: {消息}"`）。
"""

from __future__ import annotations

import asyncio
import re
from enum import Enum

__all__ = ["ErrorClass", "classify_error", "classify_error_detail"]


class ErrorClass(str, Enum):
    """失败自愈的三分类。新增成员属于兼容性扩展。"""

    RETRYABLE = "retryable"
    NON_RETRYABLE = "non_retryable"
    NEEDS_HUMAN = "needs_human"


# 各 SDK / 标准库的瞬时故障异常类名（不引入硬依赖，按类名匹配）
_RETRYABLE_TYPES = frozenset(
    {
        "APIConnectionError",
        "APITimeoutError",
        "BrokenPipeError",
        "ConnectError",
        "ConnectTimeout",
        "ConnectionError",
        "ConnectionResetError",
        "InternalServerError",
        "NetworkError",
        "OSError",
        "PoolTimeout",
        "ProtocolError",
        "RateLimitError",
        "ReadTimeout",
        "ReadTimeoutError",
        "RemoteProtocolError",
        "ServiceUnavailableError",
        "TemporaryFailure",
        "WriteTimeout",
    }
)
_HUMAN_TYPES = frozenset({"PermissionError", "AuthenticationError", "AuthorizationError"})
_RETRYABLE_MARKERS = (
    "429",
    "500",
    "502",
    "503",
    "504",
    "bad gateway",
    "connection reset",
    "overloaded",
    "temporarily unavailable",
    "timed out",
    "timeout",
    "too many requests",
)
_HUMAN_MARKERS = (
    "401",
    "403",
    "api key",
    "approval",
    "forbidden",
    "insufficient quota",
    "invalid api key",
    "not authorized",
    "permission denied",
    "unauthorized",
)
_STATUS_PATTERNS = (
    re.compile(r"status(?:_|\s)?code\D{0,3}(\d{3})"),
    re.compile(r"error code\D{0,3}(\d{3})"),
    re.compile(r"HTTP/\d(?:\.\d)?\D{0,3}(\d{3})"),
)


def _classify_code(code: int) -> ErrorClass:
    if code in (408, 409, 425, 429) or code >= 500:
        return ErrorClass.RETRYABLE
    if code in (401, 402, 403, 451):
        return ErrorClass.NEEDS_HUMAN
    return ErrorClass.NON_RETRYABLE


def _attribute_status(exc: BaseException) -> int | None:
    for holder in (exc, getattr(exc, "response", None)):
        code = getattr(holder, "status_code", None) if holder is not None else None
        if isinstance(code, int):
            return code
    return None


def _find_status(text: str) -> int | None:
    for pattern in _STATUS_PATTERNS:
        match = pattern.search(text)
        if match:
            return int(match.group(1))
    return None


def classify(type_name: str, message: str) -> ErrorClass:
    """分类判据的唯一实现：入参为已归一化的类型名与消息文本。"""
    lowered = f"{type_name} {message}".lower()
    code = _find_status(lowered)
    if code is not None:
        return _classify_code(code)
    if type_name in _HUMAN_TYPES:
        return ErrorClass.NEEDS_HUMAN
    if type_name in _RETRYABLE_TYPES:
        return ErrorClass.RETRYABLE
    if any(marker in lowered for marker in _HUMAN_MARKERS):
        return ErrorClass.NEEDS_HUMAN
    if any(marker in lowered for marker in _RETRYABLE_MARKERS):
        return ErrorClass.RETRYABLE
    return ErrorClass.NON_RETRYABLE


def classify_error(exc: BaseException) -> ErrorClass:
    """按异常对象分类：状态码 > 类型名 > 消息特征 > 默认不可重试。"""
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError, ConnectionError)):
        return ErrorClass.RETRYABLE
    if isinstance(exc, PermissionError):
        return ErrorClass.NEEDS_HUMAN
    code = _attribute_status(exc)
    if code is None:
        code = _find_status(str(exc).lower())
    if code is not None:
        return _classify_code(code)
    return classify(type(exc).__name__, str(exc))


def classify_error_detail(detail: str) -> ErrorClass:
    """分类 P1 归一化后的错误字符串（`AgentState.error` / error 事件 detail）。"""
    name, _, message = detail.partition(":")
    return classify(name.strip(), message)
