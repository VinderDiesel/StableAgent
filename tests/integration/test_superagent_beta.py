"""WP-6.6：示例 06 离线演练与只读 Beta 报告（全离线，无 token、无公网）。"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from stableagent.orchestration.model import SystemClock
from stableagent.supervisor.store import SupervisorStore
from stableagent.supervisor.views import beta_report

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "06_superagent_beta.py"


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


async def test_run_phase_rehearses_the_beta_scenario(tmp_path: Path) -> None:
    code, out = await _run_example("--phase", "run", "--dir", str(tmp_path / "demo"))
    assert code == 0, out
    assert (
        "BETA_RESULT tickets=3 resumed=1 external_comments=1 "
        "duplicate_tickets=0 bot_triggers=0" in out
    )
    lines = [json.loads(line) for line in out.splitlines() if line.startswith("{")]
    assert any(line.get("event") == "resume" for line in lines)

    store = SupervisorStore(tmp_path / "demo" / "beta.db", clock=SystemClock())
    try:
        tickets = store.list_tickets()
        assert len(tickets) == 3
        assert len({ticket.mandate_id for ticket in tickets}) == 3
        assert sorted(ticket.status.value for ticket in tickets) == ["failed", "ready", "waiting"]
        resumed = [
            ticket
            for ticket in tickets
            if any(a.attempt == 2 for a in store.list_assignments(ticket.id))
        ]
        assert len(resumed) == 1
        assert all(trigger.actor != "agent" for trigger in store.list_triggers())
        report = beta_report(store)
        assert report.would_accept == 1
        assert report.recovered_assignments == 1
        assert report.approvals.get("comment_issue:approved") == 1
        assert report.effects.get("github_comment:succeeded") == 1
        assert sum(report.notification_counts.values()) >= 3
    finally:
        store.close()
