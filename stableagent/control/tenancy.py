"""租户上下文与安全路径（P7 M5 / WP-7.5）：tenant key + 行过滤，非独立库。"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict, field_validator

from stableagent.observability import DEFAULT_TENANT


class TenancyError(Exception):
    def __init__(self, code: str, reason: str) -> None:
        self.code = code
        self.reason = reason
        super().__init__(f"{code}: {reason}")


class TenantMismatchError(TenancyError):
    def __init__(self, reason: str) -> None:
        super().__init__("tenant.mismatch", reason)


class TenantPathError(TenancyError):
    def __init__(self, reason: str) -> None:
        super().__init__("tenant.path", reason)


class TenantContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str = DEFAULT_TENANT

    @field_validator("tenant_id")
    @classmethod
    def _nonempty(cls, value: str) -> str:
        if not value or "/" in value or ".." in value or value.strip() != value:
            raise ValueError("invalid tenant_id")
        return value


def assert_same_tenant(expected: str, actual: str) -> None:
    if expected != actual:
        raise TenantMismatchError(f"expected tenant {expected!r}, got {actual!r}")


def tenant_path(root: str | Path, tenant_id: str, *parts: str) -> Path:
    """在 root 下拼接 tenant 边界路径；拒绝绝对段与 `..`。"""
    TenantContext(tenant_id=tenant_id)  # validate
    base = Path(root).resolve()
    segments: list[str] = [tenant_id]
    for part in parts:
        if not part or part.startswith("/") or part.startswith("\\"):
            raise TenantPathError(f"absolute path segment rejected: {part!r}")
        pure = PurePosixPath(part.replace("\\", "/"))
        if pure.is_absolute() or ".." in pure.parts or pure.parts == (".",):
            raise TenantPathError(f"unsafe path segment: {part!r}")
        segments.extend(pure.parts)
    candidate = base.joinpath(*segments).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as error:
        raise TenantPathError("path escapes tenant root") from error
    # 必须仍以 tenant_id 为首段
    rel = candidate.relative_to(base)
    if not rel.parts or rel.parts[0] != tenant_id:
        raise TenantPathError("path missing tenant boundary")
    return candidate
