"""P7 M5：FastAPI 路由 RBAC / 跨租户 / SSE / 审计。"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from stableagent.api.app import create_app
from stableagent.api.state import ApiAuditLog
from stableagent.observability.emitter import DomainEventEmitter
from stableagent.observability.events import DomainEventKind
from stableagent.orchestration.model import Mandate, Ticket, TicketStatus
from stableagent.supervisor.console import Console, ConsoleConfig
from stableagent.supervisor.store import SupervisorStore
from stableagent.transport.sse import encode_sse
from tests.support import FixedClock, SequenceIds


def _console(store: SupervisorStore, clock: FixedClock) -> Console:
    return Console(
        store,
        clock=clock,
        ids=SequenceIds(),
        config=ConsoleConfig(
            repository="o/r",
            operators=frozenset({"alice", "bob"}),
            actors=frozenset({"alice", "bob"}),
            actions=("read_repository", "comment_issue"),
        ),
    )


def _client(
    tmp_path: Path, *, tenant_id: str = "default"
) -> tuple[TestClient, ApiAuditLog, SupervisorStore]:
    clock = FixedClock(1_000)
    store = SupervisorStore(tmp_path / "api.db", clock=clock)
    with store.transaction():
        store.insert_mandate(
            Mandate(
                id="md-1",
                origin_kind="github",
                scope="github:o/r:issue:1",
                granted_at=1,
                repository="o/r",
                actions=["comment_issue"],
            )
        )
        store.insert_ticket(
            Ticket(
                id="tk-1",
                title="Fix",
                task="do",
                created_at=1,
                status=TicketStatus.RUNNING,
                mandate_id="md-1",
            )
        )
    emitter = DomainEventEmitter(store, clock=clock, ids=SequenceIds())
    emitter.emit(
        DomainEventKind.EFFECT_SUCCEEDED,
        outcome="ok",
        ticket_id="tk-1",
        attrs={"token": "should-redact"},
    )
    audit = ApiAuditLog()
    app = create_app(
        _console(store, clock), store, tenant_id=tenant_id, audit=audit
    )
    return TestClient(app), audit, store


def _headers(role: str, *, tenant: str = "default", actor: str = "alice") -> dict[str, str]:
    return {
        "X-Tenant-Id": tenant,
        "X-Actor-Id": actor,
        "X-Roles": role,
    }


def test_status_viewer_ok_and_cross_tenant_denied(tmp_path: Path) -> None:
    client, _, _ = _client(tmp_path, tenant_id="tenant-a")
    ok = client.get("/v1/status", headers=_headers("viewer", tenant="tenant-a"))
    assert ok.status_code == 200
    assert ok.json()["tenant_id"] == "tenant-a"
    denied = client.get("/v1/status", headers=_headers("viewer", tenant="tenant-b"))
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "tenant.mismatch"


def test_viewer_cannot_pause(tmp_path: Path) -> None:
    client, audit, _ = _client(tmp_path)
    res = client.post(
        "/v1/runtime/pause",
        headers=_headers("viewer"),
        json={},
    )
    assert res.status_code == 403
    assert audit.entries == []


def test_operator_pause_audited(tmp_path: Path) -> None:
    client, audit, _ = _client(tmp_path)
    res = client.post(
        "/v1/runtime/pause",
        headers=_headers("operator", actor="alice"),
        json={"confirm": "pause"},
    )
    assert res.status_code == 200
    assert res.json()["runtime"]["paused"] is True
    assert any(e["action"] == "pause" for e in audit.entries)


def test_ticket_and_sse(tmp_path: Path) -> None:
    client, _, _ = _client(tmp_path)
    ticket = client.get("/v1/tickets/tk-1", headers=_headers("viewer"))
    assert ticket.status_code == 200
    assert ticket.json()["ticket"]["id"] == "tk-1"

    with client.stream(
        "GET",
        "/v1/events/tickets/tk-1",
        headers=_headers("viewer"),
    ) as stream:
        body = b"".join(stream.iter_bytes()).decode()
    assert "effect.succeeded" in body or "stream.end" in body
    assert "should-redact" not in body


def test_health_and_ui(tmp_path: Path) -> None:
    client, _, _ = _client(tmp_path)
    assert client.get("/healthz").json()["status"] == "ok"
    ui = client.get("/")
    assert ui.status_code == 200
    assert "StableAgent" in ui.text


def test_encode_helper_used() -> None:
    # 保证 encode 可被路由侧复用（烟雾）
    assert "event:" in encode_sse(
        __import__("stableagent.transport.sse", fromlist=["SseEvent"]).SseEvent(
            id="1", event="x", data={}
        )
    )
