"""进程内计数器：按 name / labels 聚合；不跨进程。"""

from __future__ import annotations

from collections import defaultdict
from threading import Lock


class Metrics:
    def __init__(self) -> None:
        self._counts: dict[tuple[str, tuple[tuple[str, str], ...]], int] = defaultdict(int)
        self._lock = Lock()

    def inc(self, name: str, *, labels: dict[str, str] | None = None, amount: int = 1) -> None:
        key = (name, tuple(sorted((labels or {}).items())))
        with self._lock:
            self._counts[key] += amount

    def get(self, name: str, *, labels: dict[str, str] | None = None) -> int:
        key = (name, tuple(sorted((labels or {}).items())))
        with self._lock:
            return self._counts.get(key, 0)

    def snapshot(self) -> dict[str, int]:
        """扁平快照：``name|k=v`` → count，便于断言。"""
        with self._lock:
            out: dict[str, int] = {}
            for (name, labels), value in self._counts.items():
                if labels:
                    suffix = ",".join(f"{k}={v}" for k, v in labels)
                    out[f"{name}|{suffix}"] = value
                else:
                    out[name] = value
            return out
