"""G2：SIGKILL 后新进程 recover() 终态正确，且已完成 Assignment 不重跑。

击杀时机由"第 2 个报告"这一确定性 stdout 事件触发，不用 sleep 赌时序（ADR-02 后果 5）。
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sqlite3
import sys
from pathlib import Path

CHILD = Path(__file__).resolve().parent / "ticket_recovery_child.py"


async def _spawn(phase: str, db: Path) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        sys.executable,
        str(CHILD),
        "--phase",
        phase,
        "--dir",
        str(db),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )


async def _run_then_kill(db: Path) -> None:
    proc = await _spawn("run", db)
    assert proc.stdout is not None
    reports: list[str] = []
    while True:
        raw = await proc.stdout.readline()
        if not raw:
            break
        line = raw.decode().strip()
        if not line.startswith("{"):
            continue
        payload = json.loads(line)
        if payload.get("report"):
            reports.append(payload["report"])
            if len(reports) == 2:
                os.kill(proc.pid, signal.SIGKILL)  # 第 2 个 Assignment 报告之后立即杀
                assert await proc.wait() != 0
                return
    raise AssertionError("子进程没有产出两个 Assignment 报告就结束了")


def _assignment_rows(db: Path) -> list[tuple[str, str]]:
    conn = sqlite3.connect(db / "orchestration.db")
    try:
        return [
            (row[0], row[1])
            for row in conn.execute("SELECT id, status FROM assignments ORDER BY id").fetchall()
        ]
    finally:
        conn.close()


async def test_crash_mid_flight_then_recover_finishes(tmp_path: Path) -> None:
    db = tmp_path / "drill"
    db.mkdir()
    await _run_then_kill(db)
    before = dict(_assignment_rows(db))
    assert any(status == "running" for status in before.values())

    proc = await _spawn("recover", db)
    assert proc.stdout is not None
    out = (await proc.stdout.read()).decode()
    assert await proc.wait() == 0
    after = dict(_assignment_rows(db))
    recovered = [json.loads(line) for line in out.splitlines() if line.startswith("{")]
    assert recovered[0]["recover"]["interrupted_assignments"] >= 1
    ended_before = {key for key, status in before.items() if status == "ended"}
    assert ended_before, "演练应至少留下一个已完成的 Assignment"
    assert all(after[key] == "ended" for key in ended_before)  # 已完成不重跑
    assert all(status != "running" for status in after.values())  # 全部收敛
