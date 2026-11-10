"""MemoryPolicy：写入门闸（origin、分类、去重、TTL、容量）。"""

from __future__ import annotations

import hashlib
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from stableagent.memory.errors import MemoryPolicyDeniedError
from stableagent.memory.model import (
    MemoryClassification,
    MemoryLayer,
    MemoryRecord,
    MemoryScope,
    WriteOrigin,
    classification_rank,
)
from stableagent.observability.redaction import redact_value


class MemoryPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    allowed_origins: frozenset[str] = Field(
        default_factory=lambda: frozenset(
            {"worker_summary", "verified_fact", "operator", "policy", "import"}
        )
    )
    max_classification: MemoryClassification = MemoryClassification.CONFIDENTIAL
    max_content_chars: int = 4_000
    short_ttl_ms: int = 86_400_000
    long_ttl_ms: int | None = 31_536_000_000
    max_items_per_scope: int = 100
    reject_secret: bool = True

    def content_digest(self, content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def sanitize_content(self, content: str) -> str:
        cleaned = redact_value(content, max_chars=self.max_content_chars)
        if not isinstance(cleaned, str):
            cleaned = str(cleaned)
        if len(cleaned) > self.max_content_chars:
            cleaned = cleaned[: self.max_content_chars - 1] + "…"
        return cleaned

    def resolve_expires_at(self, layer: MemoryLayer, *, now_ms: int) -> int | None:
        if layer is MemoryLayer.SHORT:
            return now_ms + self.short_ttl_ms
        if self.long_ttl_ms is None:
            return None
        return now_ms + self.long_ttl_ms

    def assert_writable(
        self,
        *,
        origin: WriteOrigin | str,
        classification: MemoryClassification,
        content: str,
        existing_count: int,
        duplicate: MemoryRecord | None,
    ) -> str:
        if origin not in self.allowed_origins:
            raise MemoryPolicyDeniedError(f"origin not allowed: {origin}")
        if self.reject_secret and classification is MemoryClassification.SECRET:
            raise MemoryPolicyDeniedError("secret classification rejected")
        if classification_rank(classification) > classification_rank(self.max_classification):
            raise MemoryPolicyDeniedError(
                f"classification {classification.value} exceeds max "
                f"{self.max_classification.value}"
            )
        if not content or not content.strip():
            raise MemoryPolicyDeniedError("content empty")
        if duplicate is not None and duplicate.deleted_at is None:
            raise MemoryPolicyDeniedError("duplicate content_digest in scope")
        if existing_count >= self.max_items_per_scope:
            raise MemoryPolicyDeniedError(
                f"scope capacity exceeded ({self.max_items_per_scope})"
            )
        return self.sanitize_content(content)


def scope_key(scope: MemoryScope) -> tuple[str, str]:
    return (scope.kind.value, scope.id)


def provenance_dict(**extra: Any) -> dict[str, Any]:
    return dict(extra)
