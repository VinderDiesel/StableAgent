"""部署配置 schema（P7 M6）：校验用，不承载运行时密钥明文。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from stableagent.observability import DEFAULT_TENANT


class DeployConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str = DEFAULT_TENANT
    state_dir: str = "./state"
    db_filename: str = "stableagent.db"
    listen_host: str = "0.0.0.0"
    listen_port: int = Field(default=8080, ge=1, le=65535)
    secret_refs: tuple[str, ...] = ()
    log_retention_days: int = Field(default=30, ge=1)

    @field_validator("tenant_id", "state_dir", "db_filename")
    @classmethod
    def _nonempty(cls, value: str) -> str:
        if not value or ".." in value:
            raise ValueError("invalid path or tenant")
        return value
