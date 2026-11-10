"""入库前脱敏：键名命中、过长字符串、疑似本地绝对路径。"""

from __future__ import annotations

import re
from typing import Any

REDACTED = "[REDACTED]"
DEFAULT_MAX_CHARS = 200

_SECRET_KEY = re.compile(
    r"(token|secret|password|passwd|authorization|api[_-]?key|bearer|credential)",
    re.IGNORECASE,
)
_ABS_PATH = re.compile(r"(^|[\s\"'=])/?(?:Users|home|root)/[^\s\"']+")


def redact_value(value: Any, *, max_chars: int = DEFAULT_MAX_CHARS) -> Any:
    if isinstance(value, dict):
        return redact_mapping(value, max_chars=max_chars)
    if isinstance(value, list):
        return [redact_value(item, max_chars=max_chars) for item in value]
    if isinstance(value, str):
        text = _ABS_PATH.sub(r"\1[REDACTED_PATH]", value)
        if len(text) > max_chars:
            return text[: max_chars - 1] + "…"
        return text
    return value


def redact_mapping(data: dict[str, Any], *, max_chars: int = DEFAULT_MAX_CHARS) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in data.items():
        if _SECRET_KEY.search(str(key)):
            out[key] = REDACTED
        else:
            out[key] = redact_value(value, max_chars=max_chars)
    return out
