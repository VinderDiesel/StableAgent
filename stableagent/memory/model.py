"""Memory 领域模型：记录、scope、删除收据与分类。"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MemoryLayer(str, Enum):
    SHORT = "short"
    LONG = "long"


class MemoryScopeKind(str, Enum):
    TICKET = "ticket"
    ASSIGNMENT = "assignment"
    TENANT = "tenant"
    REPOSITORY = "repository"


class MemoryClassification(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    SECRET = "secret"


_CLASSIFICATION_RANK = {
    MemoryClassification.PUBLIC: 0,
    MemoryClassification.INTERNAL: 1,
    MemoryClassification.CONFIDENTIAL: 2,
    MemoryClassification.SECRET: 3,
}


def classification_rank(value: MemoryClassification) -> int:
    return _CLASSIFICATION_RANK[value]


class MemoryScope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: MemoryScopeKind
    id: str

    @field_validator("id")
    @classmethod
    def _id_nonempty(cls, value: str) -> str:
        if not value:
            raise ValueError("scope id 不能为空")
        return value


class MemoryRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    tenant_id: str
    layer: MemoryLayer
    scope_kind: MemoryScopeKind
    scope_id: str
    origin: str
    content: str
    content_digest: str
    embedding_ref: str | None = None
    created_at: int
    updated_at: int
    expires_at: int | None = None
    confidence: float = 1.0
    classification: MemoryClassification = MemoryClassification.INTERNAL
    deleted_at: int | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)

    @field_validator("tenant_id")
    @classmethod
    def _tenant_nonempty(cls, value: str) -> str:
        if not value:
            raise ValueError("tenant_id 不能为空")
        return value

    @property
    def scope(self) -> MemoryScope:
        return MemoryScope(kind=self.scope_kind, id=self.scope_id)

    def is_active(self, *, now_ms: int) -> bool:
        if self.deleted_at is not None:
            return False
        return not (self.expires_at is not None and self.expires_at <= now_ms)


class EraseReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    timestamp: int
    actor: str
    tenant_id: str
    count: int
    memory_ids: tuple[str, ...] = ()


class MemoryAuthContext(BaseModel):
    """检索授权：仅返回当前主体有权读取的 scope。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str
    allowed_scopes: frozenset[MemoryScope] = Field(default_factory=frozenset)
    layers: frozenset[MemoryLayer] = Field(
        default_factory=lambda: frozenset({MemoryLayer.SHORT, MemoryLayer.LONG})
    )


class RetrievedMemory(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    record: MemoryRecord
    score: float
    token_estimate: int


WriteOrigin = Literal[
    "worker_summary",
    "verified_fact",
    "operator",
    "policy",
    "import",
]
