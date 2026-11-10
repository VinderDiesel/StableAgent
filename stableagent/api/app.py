"""FastAPI 应用工厂。"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from stableagent.api.routes_actions import router as actions_router
from stableagent.api.routes_sse import router as sse_router
from stableagent.api.routes_status import router as status_router
from stableagent.api.state import ApiAuditLog, AppState
from stableagent.observability import DEFAULT_TENANT
from stableagent.supervisor.console import Console
from stableagent.supervisor.store import SupervisorStore

_STATIC = Path(__file__).resolve().parent / "static"


def create_app(
    console: Console,
    store: SupervisorStore,
    *,
    tenant_id: str = DEFAULT_TENANT,
    audit: ApiAuditLog | None = None,
) -> FastAPI:
    app = FastAPI(title="StableAgent API", version="0.1.0")
    app.state.api = AppState(
        console=console,
        store=store,
        tenant_id=tenant_id,
        audit=audit or ApiAuditLog(),
    )
    app.include_router(status_router)
    app.include_router(actions_router)
    app.include_router(sse_router)

    if _STATIC.is_dir():
        app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")

        @app.get("/")
        def attention_board() -> FileResponse:
            return FileResponse(_STATIC / "index.html")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "tenant_id": tenant_id}

    return app
