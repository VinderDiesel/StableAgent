"""M3.7：基准脚本的离线冒烟——固定小规模可复跑，summary 字段即对外口径。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

BENCH = Path(__file__).resolve().parents[2] / "benchmarks" / "orchestration_benchmark.py"


def test_benchmark_runs_at_a_small_fixed_scale(tmp_path: Path) -> None:
    proc = subprocess.run(
        [
            sys.executable,
            str(BENCH),
            "--tickets",
            "3",
            "--assignments",
            "6",
            "--repeat",
            "1",
            "--dir",
            str(tmp_path / "bench"),
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    summary = json.loads(proc.stdout.strip().splitlines()[-1])["summary"]
    assert summary["tickets"] == 3 and summary["assignments"] == 6
    assert summary["tick_ms"] >= 0 and summary["recover_ms"] >= 0
