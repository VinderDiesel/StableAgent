"""API 应用状态：绑定 tenant + Console 门面 + 审计钩子。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from stableagent.supervisor.console import Console
from stableagent.supervisor.store import SupervisorStore


@dataclass
class ApiAuditLog:
    entries: list[dict[str, Any]] = field(default_factory=list)

    def record(self, **entry: Any) -> None:
        self.entries.append(dict(entry))


@dataclass
class AppState:
    """create_app 注入；写路径只经 console，SSE 只读 store.domain_events。"""

    console: Console
    store: SupervisorStore
    tenant_id: str
    audit: ApiAuditLog = field(default_factory=ApiAuditLog)
