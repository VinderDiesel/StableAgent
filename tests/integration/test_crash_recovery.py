"""A3：进程级 kill → 重启 → 从最近 checkpoint 恢复跑完（真实子进程，CI 离线可跑）。

与 examples/03_checkpoint_resume.py 走同一条代码路径，只是把"击杀时机"交给测试掌握：
读到第 2 步的 `checkpoint` 事件（此刻快照已 commit 落盘）立即 SIGKILL。
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sqlite3
import sys
from pathlib import Path
from typing import Any

from stableagent.core.state import AgentState, AgentStatus, EventType

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "03_checkpoint_resume.py"
KILL_AFTER_STEP = 2
TOTAL_STEPS = 4  # 击杀点之后还剩两步：SIGKILL 才不会与子进程正常退出抢那一两毫秒


async def _spawn(*args: str) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        sys.executable,
        str(EXAMPLE),
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )


async def _run_and_kill(workdir: Path, db_path: Path) -> str:
    proc = await _spawn("--phase", "run", "--dir", str(workdir), "--delay", "0.5")
    assert proc.stdout is not None
    run_id = ""
    while True:
        raw = await proc.stdout.readline()
        if not raw:
            raise AssertionError("子进程没产出 checkpoint 就退出了")
        line = raw.decode().strip()
        if not line.startswith("{"):
            continue
        payload: dict[str, Any] = json.loads(line)
        if payload.get("type") != EventType.CHECKPOINT.value:
            continue
        data = payload["data"]
        run_id = str(data["run_id"])
        if int(data["step"]) >= KILL_AFTER_STEP:
            os.kill(proc.pid, signal.SIGKILL)
            break
    assert await proc.wait() != 0, "被 SIGKILL 的进程不该正常退出"
    return run_id


def _steps_and_payloads(db_path: Path) -> list[tuple[int, str]]:
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute("SELECT step, payload FROM checkpoints ORDER BY step ASC")
        return [(int(row[0]), str(row[1])) for row in cursor.fetchall()]
    finally:
        conn.close()


async def test_a1_a2_kill_then_resume_completes_without_replaying_steps(tmp_path: Path) -> None:
    workdir = tmp_path / "ckpt"
    workdir.mkdir()
    db_path = workdir / "checkpoints.sqlite"

    run_id = await _run_and_kill(workdir, db_path)

    persisted = _steps_and_payloads(db_path)
    assert [step for step, _ in persisted] == [1, 2], "崩溃前只留下已完成步的快照"
    broken = AgentState.loads(persisted[-1][1])
    assert broken.step == KILL_AFTER_STEP
    assert len(broken.messages) == 1 + 2 * KILL_AFTER_STEP  # user + (assistant, tool) × 步数

    proc = await _spawn(
        "--phase", "resume", "--dir", str(workdir), "--run-id", run_id, "--delay", "0"
    )
    output = (await proc.stdout.read()).decode() if proc.stdout else ""
    exit_code = await proc.wait()
    assert exit_code == 0, output

    summary = next(line for line in output.splitlines() if line.startswith("RESUME_RESULT"))
    fields = dict(pair.split("=") for pair in summary.split()[1:])
    assert fields["status"] == AgentStatus.SUCCEEDED.value
    assert int(fields["steps"]) == TOTAL_STEPS
    assert int(fields["provider_calls"]) == TOTAL_STEPS - KILL_AFTER_STEP, "已完成步不重跑"

    final_rows = _steps_and_payloads(db_path)
    assert [step for step, _ in final_rows] == list(range(1, TOTAL_STEPS + 1))
    assert AgentState.loads(final_rows[-1][1]).status is AgentStatus.SUCCEEDED


async def test_selftest_phase_runs_as_one_command(tmp_path: Path) -> None:
    """对外演示的一条命令（--phase selftest）自带 kill/恢复闭环，必须独立可跑。"""
    proc = await _spawn(
        "--phase", "selftest", "--dir", str(tmp_path / "selftest"), "--delay", "0.4"
    )
    output = (await proc.stdout.read()).decode() if proc.stdout else ""
    assert await proc.wait() == 0, output
    assert "SELFTEST ok=True" in output
