"""可选 HTTP API（P7 M5）：需安装 ``stableagent[server]``（FastAPI）。"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI

    from stableagent.api.state import ApiAuditLog
    from stableagent.supervisor.console import Console
    from stableagent.supervisor.store import SupervisorStore

__all__ = ["create_app", "require_server_extra"]


def require_server_extra() -> None:
    try:
        importlib.import_module("fastapi")
    except ImportError as error:
        raise ImportError(
            "stableagent.api requires the 'server' extra; "
            "install with: pip install 'stableagent[server]'"
        ) from error


def create_app(
    console: Console,
    store: SupervisorStore,
    *,
    tenant_id: str = "default",
    audit: ApiAuditLog | None = None,
) -> FastAPI:
    require_server_extra()
    from stableagent.api.app import create_app as _create_app

    return _create_app(console, store, tenant_id=tenant_id, audit=audit)
