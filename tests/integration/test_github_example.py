"""G1/G2：示例 05 在没有网络、没有 token 的本地装置里走完受控链路与五类拒绝。"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

from stableagent.orchestration.model import SystemClock
from stableagent.orchestration.store import Store

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "05_guarded_github_task.py"


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


async def test_run_phase_guards_the_whole_chain(tmp_path: Path) -> None:
    code, out = await _run_example("--phase", "run", "--dir", str(tmp_path / "demo"))
    assert code == 0, out
    assert "RUN_RESULT status=completed" in out
    lines = [json.loads(line) for line in out.splitlines() if line.startswith("{")]
    ticket = next(line["ticket"] for line in lines if "ticket" in line)
    branch = next(line["branch"] for line in lines if "branch" in line)
    assert sum(1 for line in lines if line.get("event") == "pr") == 1

    remote = tmp_path / "demo" / "remote.git"
    names = subprocess.run(
        ["git", "--git-dir", str(remote), "branch", "--list", "sa-*"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert branch in names
    subject = subprocess.run(
        ["git", "--git-dir", str(remote), "log", "-1", "--format=%s", branch],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert subject == "fix off-by-one"

    store = Store(tmp_path / "demo" / "guarded.db", clock=SystemClock())
    try:
        audits = store.list_audit(ticket_id=ticket)
        ordered = [(row["kind"], row["action"]) for row in audits]
        assert ("revalidate", "worktree") in ordered
        assert ordered.index(("revalidate", "worktree")) < ordered.index(
            ("authorize", "write_repository")
        )
        assert ordered.index(("authorize", "write_repository")) < ordered.index(
            ("authorize", "create_pull_request")
        )
    finally:
        store.close()


async def test_reject_phase_has_five_structured_refusals(tmp_path: Path) -> None:
    code, out = await _run_example("--phase", "reject", "--dir", str(tmp_path / "reject"))
    assert code == 0, out
    assert "REJECT_RESULT cases=5 ok=True" in out
    lines = [json.loads(line) for line in out.splitlines() if line.startswith("{")]
    refusals = [line for line in lines if "reject" in line]
    assert [line["reject"] for line in refusals] == [
        "actor_not_allowlisted",
        "mention_required",
        "command_not_allowed",
        "approval_pending",
        "auto_merge_not_grantable",
    ]
    assert all(line["external_calls"] == 0 for line in refusals)
    assert all(line["audit"] >= 1 for line in refusals)
    assert [line["effects"] for line in refusals[3:]] == [1, 0]
