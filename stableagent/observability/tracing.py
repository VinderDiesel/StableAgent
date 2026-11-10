"""以 ticket_id 为根的轻量关联：记录 child 对象 id，不引入外部 tracing SDK。"""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock


@dataclass
class TraceContext:
    root_ticket_id: str
    children: list[str] = field(default_factory=list)

    def link(self, object_id: str) -> None:
        if object_id and object_id not in self.children:
            self.children.append(object_id)


class TraceRegistry:
    def __init__(self) -> None:
        self._roots: dict[str, TraceContext] = {}
        self._lock = Lock()

    def for_ticket(self, ticket_id: str) -> TraceContext:
        with self._lock:
            ctx = self._roots.get(ticket_id)
            if ctx is None:
                ctx = TraceContext(root_ticket_id=ticket_id)
                self._roots[ticket_id] = ctx
            return ctx

    def link(self, ticket_id: str, object_id: str) -> TraceContext:
        ctx = self.for_ticket(ticket_id)
        ctx.link(object_id)
        return ctx

    def get(self, ticket_id: str) -> TraceContext | None:
        with self._lock:
            return self._roots.get(ticket_id)
