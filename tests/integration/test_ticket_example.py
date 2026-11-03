"""M3.1：示例即测试——run 面保证父子 Ticket、依赖顺序与逐行 JSON 约定不腐烂。

测试以子进程跑示例，不 import 示例模块：示例只依赖 stableagent.* 与 stdlib。
selftest 面在 Task 24 追加。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "04_ticket_orchestration.py"


async def _run_example(*args: str) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        str(EXAMPLE),
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    assert proc.stdout is not None
    out = (await proc.stdout.read()).decode()
    return await proc.wait(), out


async def test_run_phase_completes_parent_and_child(tmp_path: Path) -> None:
    code, out = await _run_example(
        "--phase", "run", "--dir", str(tmp_path / "run"), "--delay", "0.05"
    )
    assert code == 0, out
    assert "RUN_RESULT status=completed tickets=2" in out
    lines = [json.loads(line) for line in out.splitlines() if line.startswith("{")]
    tickets = {line["ticket"]: line["status"] for line in lines if "status" in line}
    assert tickets == {"tk-0001": "completed", "tk-0002": "completed"}
    reports = [line for line in lines if "report" in line]
    assert len(reports) >= 3  # A 两条 progress + 两条终态 Report 至少取到 3 行
    first_child_assignment = next(
        i
        for i, line in enumerate(lines)
        if line.get("assignment") and line.get("ticket") == "tk-0002"
    )
    parent_end = max(
        i for i, line in enumerate(lines) if line.get("ticket") == "tk-0001" and "report" in line
    )
    assert parent_end < first_child_assignment  # 依赖：B 在 A 收口之后才起跑


async def test_selftest_phase_kills_then_recovers(tmp_path: Path) -> None:
    code, out = await _run_example(
        "--phase", "selftest", "--dir", str(tmp_path / "selftest"), "--delay", "0.3"
    )
    assert code == 0, out
    assert "SELFTEST ok=True killed=True completed=2" in out
